"""
Feature Registry (ADR-007) — Classifies features from user prompts into
CORE_PRODUCT, SALES_UI, and INFRASTRUCTURE categories for wave scheduling.

Root cause (Iteration 144): 55% prompt alignment because the orchestrator
treated all features equally. Sales/UI funnels got built while Core Product
features (review capture, reputation management) remained stubs.

The registry provides:
1. Feature classification by category
2. Priority-ordered checklist generation for delegation messages
3. Reconciliation after boomerang assessment
"""
from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("agix.feature_registry")


@dataclass
class Feature:
    """A single feature extracted from the user's prompt."""
    id: str
    name: str
    category: str       # "CORE_PRODUCT" | "SALES_UI" | "INFRASTRUCTURE"
    priority: int       # Wave number: 1=first, 2=second, 3=third
    status: str         # "pending" | "in_progress" | "complete" | "failed"
    assigned_to: Optional[str] = None    # agent profile
    deliverables: List[str] = field(default_factory=list)


# ── Classification keywords ──

_INFRASTRUCTURE_KEYWORDS = [
    r"auth(?:entication|orization)?",
    r"jwt",
    r"oauth",
    r"login",
    r"signup",
    r"register",
    r"payment",
    r"stripe",
    r"billing",
    r"email\s+(?:notification|integration|system|service)",
    r"smtp",
    r"resend",
    r"sendgrid",
    r"database",
    r"prisma",
    r"migration",
    r"deployment",
    r"ci/cd",
    r"docker",
    r"env(?:ironment)?\s+var",
    r"secret",
    r"api\s+key",
    r"webhook",
    r"queue",
    r"cron",
    r"scheduled?\s+(?:job|task)",
]

_SALES_UI_KEYWORDS = [
    r"landing\s+page",
    r"hero\s+(?:section|banner|area)",
    r"pricing\s+(?:page|table|tier|plan)",
    r"lead\s+(?:capture|gen|generation|form|magnet)",
    r"marketing",
    r"seo",
    r"testimonial",
    r"social\s+proof",
    r"cta|call.to.action",
    r"about\s+(?:us|page)",
    r"contact\s+(?:us|page|form)",
    r"footer",
    r"navigation\s+(?:bar|menu|header)",
    r"faq",
    r"blog",
    r"newsletter",
    r"sign\s*up\s+(?:form|page|flow)",
    r"onboarding\s+(?:flow|wizard|screen)",
    r"demo\s+(?:page|request|booking)",
]

# Compile patterns
_INFRA_PATTERNS = [re.compile(kw, re.IGNORECASE) for kw in _INFRASTRUCTURE_KEYWORDS]
_SALES_PATTERNS = [re.compile(kw, re.IGNORECASE) for kw in _SALES_UI_KEYWORDS]

# Priority mapping
_CATEGORY_PRIORITY = {
    "INFRASTRUCTURE": 1,
    "CORE_PRODUCT": 2,
    "SALES_UI": 3,
}


def classify_features(feature_names: List[str]) -> List[Feature]:
    """Classify a list of feature names into categories.

    Each feature is matched against keyword patterns for INFRASTRUCTURE
    and SALES_UI. Features that don't match either are classified as
    CORE_PRODUCT (the default — the product's primary value).

    Args:
        feature_names: List of feature name strings extracted from user prompt.

    Returns:
        List of Feature objects with category, priority, and pending status.
    """
    features = []

    for i, name in enumerate(feature_names):
        category = _classify_single(name)
        priority = _CATEGORY_PRIORITY.get(category, 2)

        features.append(Feature(
            id=f"f{i + 1}",
            name=name,
            category=category,
            priority=priority,
            status="pending",
        ))

    return features


def _classify_single(name: str) -> str:
    """Classify a single feature name into a category."""
    # Check INFRASTRUCTURE first (highest scheduling priority)
    for pattern in _INFRA_PATTERNS:
        if pattern.search(name):
            return "INFRASTRUCTURE"

    # Check SALES_UI
    for pattern in _SALES_PATTERNS:
        if pattern.search(name):
            return "SALES_UI"

    # Default: CORE_PRODUCT (the product's primary value)
    return "CORE_PRODUCT"


def generate_checklist(features: List[Feature]) -> str:
    """Generate a markdown checklist from classified features.

    Features are sorted by priority (wave number), then by category.
    Complete features get [x], pending get [ ], in-progress get [/].

    Args:
        features: List of Feature objects.

    Returns:
        Markdown-formatted checklist string.
    """
    # Sort by priority, then by category name for consistency
    sorted_features = sorted(features, key=lambda f: (f.priority, f.category, f.name))

    lines = []
    current_wave = None

    for f in sorted_features:
        # Add wave header
        if f.priority != current_wave:
            current_wave = f.priority
            if lines:
                lines.append("")  # blank line between waves
            lines.append(f"**Wave {current_wave}:**")

        # Checkbox
        if f.status == "complete":
            checkbox = "[x]"
        elif f.status == "in_progress":
            checkbox = "[/]"
        else:
            checkbox = "[ ]"

        # Profile assignment
        profile_str = f" → {f.assigned_to}" if f.assigned_to else ""

        lines.append(f"- {checkbox} {f.name} ({f.category}){profile_str}")

    return "\n".join(lines)


def reconcile_checklist(
    features: List[Feature],
    completed_ids: List[str],
) -> List[Feature]:
    """Update feature statuses after a boomerang assessment.

    Marks features whose IDs are in the completed list as 'complete'.
    Preserves already-complete features.

    Args:
        features: Current list of Feature objects.
        completed_ids: List of feature IDs that were completed.

    Returns:
        Updated list of Feature objects (new instances, not mutated).
    """
    completed_set = set(completed_ids)
    updated = []

    for f in features:
        new_status = f.status
        if f.id in completed_set:
            new_status = "complete"
        elif f.status == "complete":
            new_status = "complete"  # Preserve existing completion

        updated.append(Feature(
            id=f.id,
            name=f.name,
            category=f.category,
            priority=f.priority,
            status=new_status,
            assigned_to=f.assigned_to,
            deliverables=f.deliverables.copy(),
        ))

    return updated
