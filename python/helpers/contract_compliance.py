"""
Contract Compliance — Compliance extraction, user journeys, checklist items,
and implied feature inference from user prompts.

Extracted from prompt_contract_parser.py during modularization.
"""

import logging
import re
from typing import Dict, List, Set

from python.helpers.contract_patterns import (
    _ACTION_VERIFY_MAP,
    _CHECKBOX_MARKDOWN_RE,
    _CHECKBOX_UNICODE_RE,
    _COMPLIANCE_ACTION_RE,
    _COMPLIANCE_FRAMEWORK_RE,
    _FRAMEWORK_VERIFY_MAP,
    _IMPLICATION_RULES,
    _JOURNEY_CONDITIONAL_RE,
    _JOURNEY_DESTINATION_RE,
    _NATURAL_OPTOUT_RE,
    _OBLIGATION_CONTEXT_RE,
    _normalize_prompt_text,
)

logger = logging.getLogger("agix.prompt_contract_parser")


# ─── Compliance Extraction (U-4, RCA-302) ─────────────────────────────


def extract_compliance_requirements(prompt: str) -> List[Dict]:
    """Extract regulatory/compliance requirements from a raw prompt.

    Uses enhanced deterministic regex with linguistic patterns:
    - Explicit framework detection (CAN-SPAM, GDPR, TCPA, etc.)
    - Compliance action patterns (unsubscribe, opt-out, privacy policy)
    - Obligation context detection (must, required, mandatory)
    - Natural language opt-out patterns

    Each compliance item is a dict with:
        - name: Description of the compliance requirement
        - type: "compliance"
        - confidence: 1.0 (always enforced, never skipped)
        - verify_pattern: Regex pattern for source code verification
        - hard_requirement: True (always blocking)
        - framework: Regulatory framework (CAN-SPAM, GDPR, etc.)
        - source_sentence: The sentence that triggered extraction

    Returns:
        List of compliance requirement dicts. Empty list if no
        compliance requirements found.
    """
    if not prompt or len(prompt.strip()) < 10:
        return []

    prompt = _normalize_prompt_text(prompt)
    # Strip checkbox markers so detection works regardless of formatting
    prompt_clean = re.sub(r'[⬜✅☐☑✓✗✘□■●○]', '', prompt)

    requirements: List[Dict] = []
    seen_keys: Set[str] = set()

    def _add_requirement(
        name: str,
        framework: str,
        verify_pattern: str,
        source_sentence: str,
    ) -> None:
        """Add a compliance requirement if not already seen."""
        key = f"{framework.lower()}:{name.lower()}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        requirements.append({
            "name": f"{framework}: {name}",
            "type": "compliance",
            "confidence": 1.0,
            "verify_pattern": verify_pattern,
            "hard_requirement": True,
            "framework": framework,
            "source_sentence": source_sentence.strip()[:200],
        })

    # ── Pass 1: Explicit framework mentions ──
    # Look for named frameworks (CAN-SPAM, GDPR, etc.)
    for m in _COMPLIANCE_FRAMEWORK_RE.finditer(prompt_clean):
        framework_raw = m.group(1).strip()
        # Normalize framework name
        framework_key = re.sub(r'[\s-]+', '-', framework_raw).lower()

        # Extract the surrounding sentence for context
        start = max(0, prompt_clean.rfind('.', 0, m.start()) + 1)
        end = prompt_clean.find('.', m.end())
        if end == -1:
            end = min(len(prompt_clean), m.end() + 150)
        sentence = prompt_clean[start:end].strip()

        # Look up framework verify pattern
        verify_info = _FRAMEWORK_VERIFY_MAP.get(framework_key, {})
        verify_pattern = verify_info.get(
            "verify_pattern",
            rf"(?i)(?:{re.escape(framework_raw)})",
        )
        description = verify_info.get("description", "compliance")

        _add_requirement(
            name=description,
            framework=framework_raw.upper(),
            verify_pattern=verify_pattern,
            source_sentence=sentence,
        )

    # ── Pass 2: Compliance action patterns ──
    # Look for action keywords (unsubscribe, privacy policy, etc.)
    for m in _COMPLIANCE_ACTION_RE.finditer(prompt_clean):
        action_text = m.group(1).strip()

        # Extract surrounding sentence
        start = max(0, prompt_clean.rfind('.', 0, m.start()) + 1)
        end = prompt_clean.find('.', m.end())
        if end == -1:
            end = min(len(prompt_clean), m.end() + 150)
        sentence = prompt_clean[start:end].strip()

        # Find matching action verify info
        action_key = action_text.split()[0].lower()[:6]  # First word, truncated
        for key, info in _ACTION_VERIFY_MAP.items():
            if action_key.startswith(key[:4]):
                _add_requirement(
                    name=info["name"],
                    framework=info["framework"],
                    verify_pattern=info["verify_pattern"],
                    source_sentence=sentence,
                )
                break

    # ── Pass 3: Natural language opt-out patterns ──
    # Catches sentences like "give recipients a way to stop receiving messages"
    for m in _NATURAL_OPTOUT_RE.finditer(prompt_clean):
        start = max(0, prompt_clean.rfind('.', 0, m.start()) + 1)
        end = prompt_clean.find('.', m.end())
        if end == -1:
            end = min(len(prompt_clean), m.end() + 150)
        sentence = prompt_clean[start:end].strip()

        # Check if there's obligation context nearby
        context_start = max(0, m.start() - 100)
        context_end = min(len(prompt_clean), m.end() + 100)
        context = prompt_clean[context_start:context_end]

        if _OBLIGATION_CONTEXT_RE.search(context):
            _add_requirement(
                name="opt-out mechanism",
                framework="CAN-SPAM",
                verify_pattern=r"(?i)(?:unsubscribe|opt[\s._-]*out|STOP|stop[\s._-]*receiving|remove[\s._-]*from)",
                source_sentence=sentence,
            )

    logger.info(
        f"[PROMPT CONTRACT PARSER] Extracted {len(requirements)} compliance "
        f"requirements from prompt ({len(prompt)} chars)"
    )

    return requirements


# ─── User Journey Extraction (U-3, RCA-302) ──────────────────────────


def extract_user_journeys(prompt: str) -> List[Dict]:
    """Extract user journey flows from a raw prompt.

    Uses enhanced deterministic regex with linguistic patterns:
    - Actor-action-outcome chains
    - Conditional routing (happy/unhappy paths)
    - Sequential step markers

    Each journey item is a dict with:
        - name: Description of the journey step
        - type: "journey"
        - verify_pattern: Regex pattern for source code verification
        - step_order: Ordinal position in the journey (0 if unknown)
        - condition: Conditional context (e.g., "positive sentiment")

    Returns:
        List of journey dicts. Empty list if no journeys found.
    """
    if not prompt or len(prompt.strip()) < 15:
        return []

    prompt = _normalize_prompt_text(prompt)
    journeys: List[Dict] = []
    seen_keys: Set[str] = set()
    step_counter = 0

    def _add_journey(
        name: str,
        verify_pattern: str,
        condition: str = "",
    ) -> None:
        nonlocal step_counter
        key = name.strip().lower()
        if key in seen_keys:
            return
        seen_keys.add(key)
        step_counter += 1
        journeys.append({
            "name": name.strip(),
            "type": "journey",
            "verify_pattern": verify_pattern,
            "step_order": step_counter,
            "condition": condition,
        })

    # ── Pass 1: Conditional routing patterns ──
    for m in _JOURNEY_CONDITIONAL_RE.finditer(prompt):
        text = m.group(0).strip()

        # Determine if happy or unhappy path
        is_positive = bool(re.search(
            r'(?:positive|happy|good|high|satisfied|4[\s-]*star|5[\s-]*star)',
            text, re.IGNORECASE,
        ))
        condition = "positive sentiment" if is_positive else "negative sentiment"

        # Look for routing destination nearby
        context_end = min(len(prompt), m.end() + 200)
        nearby_text = prompt[m.start():context_end]
        dest_match = _JOURNEY_DESTINATION_RE.search(nearby_text)

        if dest_match:
            destination = dest_match.group(1).strip()
            _add_journey(
                name=f"{'happy' if is_positive else 'unhappy'} path → {destination}",
                verify_pattern=rf"(?i)(?:{re.escape(destination.split()[0])}|redirect|route|{'positive' if is_positive else 'negative'}|{'happy' if is_positive else 'unhappy'})",
                condition=condition,
            )
        else:
            _add_journey(
                name=f"{'happy' if is_positive else 'unhappy'} path routing",
                verify_pattern=r"(?i)(?:redirect|route|condition|if|switch|positive|negative|happy|unhappy|sentiment|rating)",
                condition=condition,
            )

    # ── Pass 2: Routing destinations without explicit conditionals ──
    for m in _JOURNEY_DESTINATION_RE.finditer(prompt):
        destination = m.group(1).strip()
        # Skip if already captured in Pass 1
        dest_key = f"→ {destination}".lower()
        if any(dest_key in k for k in seen_keys):
            continue

        start = max(0, prompt.rfind('.', 0, m.start()) + 1)
        end = prompt.find('.', m.end())
        if end == -1:
            end = min(len(prompt), m.end() + 100)
        sentence = prompt[start:end].strip()

        _add_journey(
            name=f"route to {destination}",
            verify_pattern=rf"(?i)(?:{re.escape(destination.split()[0])}|redirect|route|navigate|link)",
            condition="",
        )

    logger.info(
        f"[PROMPT CONTRACT PARSER] Extracted {len(journeys)} user journeys "
        f"from prompt ({len(prompt)} chars)"
    )

    return journeys


# ─── FIX-13: Implied Feature Inference ────────────────────────────────


def infer_implied_features(
    features: List[Dict],
    compliance: List[Dict],
) -> List[Dict]:
    """FIX-13: Infer implied requirements from existing feature patterns.

    Scans the extracted features list for patterns that imply additional
    requirements. E.g., a dashboard page implies authentication is needed.
    Returns ONLY the newly implied features (does not include originals).

    Deduplication: If the implied feature is already present in features
    or compliance, it is NOT added.

    Args:
        features: List of feature dicts from extract_features().
        compliance: List of compliance dicts from extract_compliance_requirements().

    Returns:
        List of implied feature dicts (each with implied=True marker).
    """
    implied: List[Dict] = []
    seen_implied: Set[str] = set()

    # Build a set of existing feature names for dedup
    existing_names = {f["name"].lower() for f in features}
    existing_compliance_names = {
        c.get("name", "").lower() for c in compliance
    }

    for rule in _IMPLICATION_RULES:
        # Check if ANY feature matches this rule
        matched = False
        for feat in features:
            cat = feat.get("category", "")
            name = feat.get("name", "")
            if cat in rule["trigger_categories"] and re.search(
                rule["trigger_pattern"], name
            ):
                matched = True
                break

        if not matched:
            continue

        # Check dedup: is the implied feature already present?
        dedup_kw = rule.get("dedup_keywords", set())
        already_present = any(
            kw in existing_names or any(kw in n for n in existing_names)
            for kw in dedup_kw
        )
        if already_present:
            continue

        # Check compliance dedup
        compliance_kw = rule.get("dedup_compliance_keywords", set())
        if compliance_kw:
            already_in_compliance = any(
                any(kw in cn for cn in existing_compliance_names)
                for kw in compliance_kw
            )
            if already_in_compliance:
                continue

        # Add implied feature (dedup by name)
        implied_key = rule["implied_name"].lower()
        if implied_key not in seen_implied:
            seen_implied.add(implied_key)
            implied.append({
                "name": rule["implied_name"],
                "expected_route": rule["implied_route"],
                "category": rule["implied_category"],
                "implied": True,
            })

    if implied:
        logger.info(
            f"[PROMPT CONTRACT PARSER] Inferred {len(implied)} implied features: "
            f"{', '.join(f['name'] for f in implied)}"
        )

    return implied


# ─── FIX-14: Checklist Items as Mandatory ─────────────────────────────


def extract_checklist_items(prompt: str) -> List[Dict]:
    """FIX-14: Extract ALL checklist/checkbox items as mandatory requirements.

    Every checklist item — regardless of checked (✅/☑/[x]) or unchecked
    (⬜/☐/[ ]) state — is treated as a hard requirement with confidence=1.0.

    This catches the common pattern where prompts include CAN-SPAM or
    compliance checklists with unchecked items marked "To add" — these
    are still requirements the app must implement.

    Args:
        prompt: Raw user prompt text.

    Returns:
        List of checklist requirement dicts. Each has:
        - name: The checklist item text
        - type: "checklist"
        - confidence: 1.0
        - hard_requirement: True
        - source_marker: The original checkbox character
    """
    if not prompt or len(prompt.strip()) < 10:
        return []

    items: List[Dict] = []
    seen: Set[str] = set()

    def _add_item(text: str) -> None:
        """Add a checklist item if not duplicate."""
        # Clean up common prefixes
        cleaned = re.sub(
            r'^(?:To\s+add\s*[-—:]?\s*|Need\s+to\s+add\s*[-—:]?\s*)',
            '', text, flags=re.IGNORECASE,
        ).strip()
        if len(cleaned) < 3:
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        items.append({
            "name": cleaned,
            "type": "checklist",
            "confidence": 1.0,
            "hard_requirement": True,
        })

    # Pass 1: Unicode checkbox markers
    for m in _CHECKBOX_UNICODE_RE.finditer(prompt):
        _add_item(m.group(1).strip())

    # Pass 2: Markdown-style checkboxes
    for m in _CHECKBOX_MARKDOWN_RE.finditer(prompt):
        _add_item(m.group(1).strip())

    if items:
        logger.info(
            f"[PROMPT CONTRACT PARSER] Extracted {len(items)} checklist items "
            f"as mandatory requirements"
        )

    return items
