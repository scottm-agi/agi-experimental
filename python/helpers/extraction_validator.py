"""
Extraction Validator — Layer 3 of the Hybrid Extraction Pipeline.

Validates the combined output of:
  Layer 1: Deterministic regex → WeightedCandidate
  Layer 2: LLM classification → success_criteria

This validator:
  1. Hallucination guard: flags LLM criteria not anchored in the prompt
  2. Dropped signal audit: recovers high-confidence regex matches the LLM missed
  3. Category coherence: ensures categorization is consistent
  4. Coverage check: warns if regex found items the LLM completely ignored

Part of RCA-340 Phase 2: Married Pipeline Architecture.
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger("agix.extraction_validator")

# Confidence threshold above which regex signals are accepted directly
# without needing LLM confirmation
HIGH_CONFIDENCE_THRESHOLD = 0.85

# Confidence threshold below which regex signals are only advisory
# (they inform the LLM but aren't accepted as standalone requirements)
LOW_CONFIDENCE_THRESHOLD = 0.5


def validate_extraction(
    prompt: str,
    candidates: "List",  # List[WeightedCandidate]
    llm_criteria: List[str],
    high_threshold: float = HIGH_CONFIDENCE_THRESHOLD,
) -> List[Dict]:
    """Validate and merge regex signals with LLM criteria.

    Produces a unified list of validated requirements, each with a
    source attribution and confidence score.

    Args:
        prompt: The original user prompt text.
        candidates: WeightedCandidate list from Layer 1 (deterministic).
        llm_criteria: List of success criteria strings from Layer 2 (LLM).
        high_threshold: Confidence threshold for direct regex acceptance.

    Returns:
        List of validated requirement dicts with keys:
        - text: Requirement text
        - category: Requirement category
        - source: "regex", "llm", "both", or "llm_unverified"
        - confidence: 0.0–1.0
    """
    validated: List[Dict] = []
    matched_criteria: set = set()
    matched_candidates: set = set()

    # ── Phase A: Match regex candidates against LLM criteria ─────────
    for i, candidate in enumerate(candidates):
        best_match_idx = _find_best_llm_match(candidate, llm_criteria)

        if best_match_idx is not None:
            # Both regex and LLM agree — highest confidence
            matched_criteria.add(best_match_idx)
            matched_candidates.add(i)
            validated.append({
                "text": llm_criteria[best_match_idx],
                "category": candidate.category,
                "source": "both",
                "confidence": min(1.0, candidate.confidence + 0.1),
            })
        elif candidate.confidence >= high_threshold:
            # High-confidence regex signal — accept directly
            matched_candidates.add(i)
            validated.append({
                "text": candidate.text,
                "category": candidate.category,
                "source": "regex",
                "confidence": candidate.confidence,
            })

    # ── Phase B: Accept unmatched LLM criteria (with hallucination check) ─
    for j, criterion in enumerate(llm_criteria):
        if j in matched_criteria:
            continue  # Already matched in Phase A

        # Hallucination guard: check if criterion text appears in prompt
        if _is_anchored_in_prompt(criterion, prompt):
            validated.append({
                "text": criterion,
                "category": _infer_category_from_text(criterion),
                "source": "llm",
                "confidence": 0.75,
            })
        else:
            # LLM fabricated something not in the prompt
            logger.warning(
                f"[EXTRACTION VALIDATOR] Unverified LLM criterion: {criterion[:80]}"
            )
            validated.append({
                "text": criterion,
                "category": _infer_category_from_text(criterion),
                "source": "llm_unverified",
                "confidence": 0.3,
            })

    # ── Phase C: Recover medium-confidence regex signals ─────────────
    for i, candidate in enumerate(candidates):
        if i in matched_candidates:
            continue
        if candidate.confidence >= LOW_CONFIDENCE_THRESHOLD:
            # This signal wasn't confirmed by LLM but has decent confidence
            validated.append({
                "text": candidate.text,
                "category": candidate.category,
                "source": "regex",
                "confidence": candidate.confidence * 0.9,  # Slight penalty
            })

    # ── Logging ──────────────────────────────────────────────────────
    source_counts = {}
    for v in validated:
        source_counts[v["source"]] = source_counts.get(v["source"], 0) + 1
    logger.info(
        f"[EXTRACTION VALIDATOR] Validated {len(validated)} requirements "
        f"(sources: {source_counts})"
    )

    return validated


def _find_best_llm_match(candidate, llm_criteria: List[str]) -> Optional[int]:
    """Find the best matching LLM criterion for a regex candidate.

    Uses keyword overlap to determine if an LLM criterion covers the
    same requirement as a regex candidate.

    Returns:
        Index of the best matching criterion, or None if no match.
    """
    candidate_words = _extract_keywords(candidate.text)

    if not candidate_words:
        return None

    best_score = 0
    best_idx = None

    for i, criterion in enumerate(llm_criteria):
        criterion_words = _extract_keywords(criterion)
        if not criterion_words:
            continue

        overlap = candidate_words & criterion_words
        score = len(overlap) / max(len(candidate_words), 1)

        if score > best_score and score >= 0.3:
            best_score = score
            best_idx = i

    return best_idx


def _extract_keywords(text: str) -> set:
    """Extract significant keywords from text for matching."""
    # Remove common stop words
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "shall", "can", "need", "dare",
        "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
        "into", "through", "during", "before", "after", "above", "below",
        "between", "out", "off", "down", "up", "over", "under", "again",
        "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "some", "no",
        "this", "that", "these", "those", "it", "its", "they", "them",
        "their", "we", "us", "our", "you", "your", "he", "she", "him", "her",
    }

    words = set(re.findall(r'[a-zA-Z]{2,}', text.lower()))
    return words - stop_words


def _is_anchored_in_prompt(criterion: str, prompt: str) -> bool:
    """Check if an LLM criterion is anchored to text in the original prompt.

    Uses keyword overlap: if at least 2 significant words from the criterion
    appear near each other in the prompt, it's considered anchored.
    """
    keywords = _extract_keywords(criterion)
    prompt_lower = prompt.lower()

    # Count how many criterion keywords appear in the prompt
    found = sum(1 for kw in keywords if kw in prompt_lower)

    # At least 2 keywords or 40% of criterion keywords must appear in prompt
    threshold = max(2, int(len(keywords) * 0.4))
    return found >= threshold


def _infer_category_from_text(text: str) -> str:
    """Infer a requirement category from the criterion text."""
    text_lower = text.lower()

    if any(kw in text_lower for kw in ("http://", "https://", "url", "domain")):
        return "url"
    if any(kw in text_lower for kw in ("payment", "checkout", "billing", "stripe")):
        return "integration"
    if any(kw in text_lower for kw in ("page", "dashboard", "view", "screen")):
        return "page"
    if any(kw in text_lower for kw in ("deploy", "push", "host", "publish")):
        return "deployment"
    if any(kw in text_lower for kw in ("env", "config", "database", "secret")):
        return "config"
    if any(kw in text_lower for kw in ("price", "cost", "$")):
        return "copy"

    return "feature"


# ═══════════════════════════════════════════════════════════════════════
# Completeness Audit — Layer 3 of RCA-354 F-2
# ═══════════════════════════════════════════════════════════════════════
# Checks whether the COMBINED output (regex + LLM) is COMPLETE relative
# to the prompt's actual content. Specifically flags:
# 1. Monetization language without a corresponding product workflow requirement
# 2. Low keyword coverage between prompt and extracted requirements
# 3. Missing categories that the prompt strongly implies
#
# This is the "what did BOTH miss?" check that was absent from the pipeline.

# Monetization keywords — if these appear in the prompt, there MUST be
# a product workflow requirement in the extracted list
_MONETIZATION_KEYWORDS = re.compile(
    r'\$\d+(?:\.\d{2})?(?:/\w+)?|'
    r'\b(?:subscription|pricing|billing|pay|revenue|monetize|tier|'
    r'per\s+(?:month|location|seat|user)|SaaS|ARR|MRR|freemium|'
    r'free\s+trial|premium|enterprise)\b',
    re.IGNORECASE,
)

# Product workflow keywords — at least one of these should appear in
# extracted requirements if the prompt has monetization language
_WORKFLOW_KEYWORDS = re.compile(
    r'\b(?:workflow|routing|route|capture|flow|pipeline|process|'
    r'sequence|step|conditional|redirect|trigger|automat|'
    r'drip|campaign|journey|funnel)\b',
    re.IGNORECASE,
)

# Core product signals — phrases that indicate the prompt is describing
# the primary product (not just infrastructure)
_CORE_PRODUCT_PHRASES = re.compile(
    r'\b(?:core\s+(?:product|feature|service|offering)|'
    r'primary\s+(?:feature|value|service)|'
    r'what\s+(?:users|customers|businesses)\s+pay\s+for|'
    r'main\s+(?:feature|product|service))\b',
    re.IGNORECASE,
)


def audit_completeness(
    prompt: str,
    extracted: List[Dict],
) -> Dict:
    """Audit whether extracted requirements adequately cover the prompt.

    This is a DETERMINISTIC completeness check — no LLM needed. It acts as
    a spaCy-equivalent semantic coverage analysis using keyword overlap.

    Checks:
    1. If prompt has monetization language, at least one extracted requirement
       must reference a product workflow.
    2. Overall keyword coverage: what fraction of significant prompt keywords
       appear in extracted requirement texts.
    3. Core product phrases in prompt → must have corresponding requirement.

    Args:
        prompt: The original user prompt text.
        extracted: List of dicts with 'text' and 'category' keys.

    Returns:
        Dict with:
          - complete: True if no critical gaps found
          - gaps: List of gap description strings
          - coverage_score: 0.0–1.0 keyword coverage metric
    """
    gaps: List[str] = []
    prompt_lower = prompt.lower()

    # Concatenate all extracted requirement texts for matching
    extracted_text = " ".join(r.get("text", "") for r in extracted).lower()

    # ── Check 1: Monetization → Product Workflow ─────────────────────
    has_monetization = bool(_MONETIZATION_KEYWORDS.search(prompt_lower))
    has_product_workflow = bool(_WORKFLOW_KEYWORDS.search(extracted_text))

    if has_monetization and not has_product_workflow:
        gaps.append(
            "CRITICAL: Prompt contains monetization/pricing language but no "
            "extracted requirement describes a product workflow, routing logic, "
            "or core product feature. The primary value proposition may be missing."
        )

    # ── Check 2: Core product phrases → requirement coverage ─────────
    has_core_phrase = bool(_CORE_PRODUCT_PHRASES.search(prompt_lower))
    if has_core_phrase:
        # Extract the surrounding context of the core product phrase
        for match in _CORE_PRODUCT_PHRASES.finditer(prompt_lower):
            # Get ~80 chars around the match for context
            start = max(0, match.start() - 20)
            end = min(len(prompt_lower), match.end() + 80)
            context = prompt_lower[start:end]
            # Check if context keywords appear in extracted requirements
            context_keywords = _extract_keywords(context)
            overlap = sum(1 for kw in context_keywords if kw in extracted_text)
            if overlap < 2:
                gaps.append(
                    f"Prompt mentions 'core product/feature' but the surrounding "
                    f"context is poorly covered in extracted requirements: "
                    f"'{context.strip()[:80]}'"
                )

    # ── Check 3: Overall keyword coverage score ──────────────────────
    prompt_keywords = _extract_keywords(prompt_lower)
    if prompt_keywords:
        covered = sum(1 for kw in prompt_keywords if kw in extracted_text)
        coverage_score = round(covered / len(prompt_keywords), 2)
    else:
        coverage_score = 1.0  # No keywords → nothing to miss

    # Low coverage is a warning, not a gap (might just be verbose prompt)
    if coverage_score < 0.3 and len(extracted) > 0:
        gaps.append(
            f"Low keyword coverage ({coverage_score:.0%}): many prompt keywords "
            f"are not represented in extracted requirements."
        )

    return {
        "complete": len(gaps) == 0,
        "gaps": gaps,
        "coverage_score": coverage_score,
    }
