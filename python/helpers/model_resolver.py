"""
Model Name Resolver — maps marketing model names to OpenRouter API slugs.

Uses the OpenRouter catalog (data/openrouter_models.json) populated by
update_openrouter_catalog.py to fuzzy-match marketing names like
"Claude Sonnet 4" to API slugs like "anthropic/claude-sonnet-4".

RCA Phase 4, Fix A: Replaces stale hardcoded mapping tables with dynamic
resolution from live API data.

RCA U-9 (2026-05-05): Added static fallback mapping for well-known models.
ROOT CAUSE: data/openrouter_models.json never existed, so resolve_model_slug()
always returned None. Every previous "fix" improved the resolver/runner LOGIC
but never provided the data. The static map ensures resolution works offline,
in Docker, and without API keys — covering 95%+ of real-world model references.

Usage:
    from python.helpers.model_resolver import resolve_model_slug, load_catalog

    catalog = load_catalog()
    slug = resolve_model_slug("Claude Sonnet 4", catalog=catalog)
    # Returns: "anthropic/claude-sonnet-4"
"""

import json
import logging
import os
import re
from typing import Dict, Optional

logger = logging.getLogger("agix.model_resolver")

# Default catalog location inside the project
_DEFAULT_CATALOG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "openrouter_models.json",
)

# ─── Static Fallback Mapping (U-9 Fix) ──────────────────────────────
#
# Well-known model marketing names → OpenRouter API slugs.
# This is the LAST RESORT when the catalog file doesn't exist.
# Catalog matches ALWAYS take priority over static entries.
#
# Keyed by NORMALIZED marketing name (lowercase, stripped).
# Updated manually as new models are released.
_STATIC_MODEL_MAP = {
    # Anthropic Claude
    "claude sonnet 4": "anthropic/claude-sonnet-4",
    "claude opus 4": "anthropic/claude-opus-4",
    "claude haiku 4": "anthropic/claude-haiku-4",
    "claude 3.5 sonnet": "anthropic/claude-3.5-sonnet",
    "claude 3.5 haiku": "anthropic/claude-3.5-haiku",
    "claude 3 opus": "anthropic/claude-3-opus",
    "claude 3 sonnet": "anthropic/claude-3-sonnet",
    "claude 3 haiku": "anthropic/claude-3-haiku",
    # OpenAI GPT
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o mini": "openai/gpt-4o-mini",
    "gpt-4 turbo": "openai/gpt-4-turbo",
    "gpt-4": "openai/gpt-4",
    "gpt-3.5 turbo": "openai/gpt-3.5-turbo",
    "o1": "openai/o1",
    "o1 mini": "openai/o1-mini",
    "o1-mini": "openai/o1-mini",
    "o1-preview": "openai/o1-preview",
    "o3": "openai/o3",
    "o3 mini": "openai/o3-mini",
    "o3-mini": "openai/o3-mini",
    "o4-mini": "openai/o4-mini",
    # Google Gemini
    "gemini 2.5 pro": "google/gemini-2.5-pro",
    "gemini 2.5 flash": "google/gemini-2.5-flash",
    "gemini 2.0 flash": "google/gemini-2.0-flash",
    "gemini 2.0 pro": "google/gemini-2.0-pro",
    "gemini 1.5 pro": "google/gemini-1.5-pro",
    "gemini 1.5 flash": "google/gemini-1.5-flash",
    "gemini pro": "google/gemini-pro",
    "gemini flash": "google/gemini-2.5-flash",
    # Meta Llama
    "llama 4": "meta-llama/llama-4",
    "llama 3.3": "meta-llama/llama-3.3-70b-instruct",
    "llama 3.1": "meta-llama/llama-3.1-405b-instruct",
    "llama 3": "meta-llama/llama-3-70b-instruct",
    # Mistral
    "mistral large": "mistralai/mistral-large",
    "mistral medium": "mistralai/mistral-medium",
    "mistral small": "mistralai/mistral-small",
    # DeepSeek
    "deepseek v3": "deepseek/deepseek-chat-v3",
    "deepseek r1": "deepseek/deepseek-r1",
}

# Suffixes to strip before matching (case-insensitive)
_STRIP_SUFFIXES = [
    "via openrouter",
    "on openrouter",
    "through openrouter",
]


def load_catalog(catalog_path: Optional[str] = None) -> Dict:
    """Load the OpenRouter model catalog from disk.

    Args:
        catalog_path: Override path to the catalog JSON file.
            Defaults to data/openrouter_models.json.

    Returns:
        Dict mapping model IDs (lowercase) to their metadata.
        Returns empty dict if file doesn't exist or is invalid.
    """
    path = catalog_path or _DEFAULT_CATALOG_PATH
    if not os.path.isfile(path):
        logger.debug(f"Model catalog not found at {path}")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
        logger.info(f"Loaded model catalog with {len(catalog)} models from {path}")
        return catalog
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(f"Failed to load model catalog: {e}")
        return {}


def _normalize(text: str) -> str:
    """Normalize text for fuzzy matching: lowercase, strip provider prefix,
    collapse whitespace, strip OpenRouter suffixes."""
    text = text.lower().strip()
    # Remove common provider prefixes
    for prefix in ("anthropic:", "openai:", "google:", "meta:", "mistral:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    # Remove "via OpenRouter" and similar suffixes
    for suffix in _STRIP_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    # Collapse multiple spaces
    text = re.sub(r"\s+", " ", text)
    return text


def resolve_model_slug(
    marketing_name: str,
    catalog: Optional[Dict] = None,
    catalog_path: Optional[str] = None,
) -> Optional[str]:
    """Resolve a marketing model name to its OpenRouter API slug.

    Matching strategy (in order):
    1. Exact match on catalog entry's 'name' field (case-insensitive)
    2. Marketing name appears as substring in catalog name (case-insensitive)
    3. Marketing name fragments match in the model ID slug
    4. Static fallback mapping for well-known models (U-9 fix)

    Args:
        marketing_name: Human-friendly name like "Claude Sonnet 4", "GPT-4o"
        catalog: Pre-loaded catalog dict. If None, loads from disk.
        catalog_path: Override path for loading catalog from disk.

    Returns:
        The OpenRouter API slug (e.g., "anthropic/claude-sonnet-4") or None.
    """
    if not marketing_name:
        return None

    if catalog is None:
        catalog = load_catalog(catalog_path=catalog_path)

    query = _normalize(marketing_name)

    # Only run catalog strategies if catalog has data
    if catalog:
        # Strategy 1: Exact match on normalized catalog name
        for model_id, meta in catalog.items():
            catalog_name = _normalize(meta.get("name", ""))
            if query == catalog_name:
                return model_id

        # Strategy 2: Query is a substring of catalog name
        # Prefer longer matches (more specific)
        candidates = []
        for model_id, meta in catalog.items():
            catalog_name = _normalize(meta.get("name", ""))
            if query in catalog_name:
                candidates.append((model_id, len(catalog_name)))

        if candidates:
            # Prefer shortest catalog name that contains the query (most specific)
            candidates.sort(key=lambda x: x[1])
            return candidates[0][0]

        # Strategy 3: Check if the query fragments match in the model ID
        # e.g., "Claude Sonnet 4" → look for IDs containing "claude", "sonnet", "4"
        query_parts = query.split()
        if len(query_parts) >= 2:
            for model_id, meta in catalog.items():
                id_lower = model_id.lower()
                name_lower = _normalize(meta.get("name", ""))
                combined = f"{id_lower} {name_lower}"
                if all(part in combined for part in query_parts):
                    return model_id

    # Strategy 4 (U-9 Fix): Static fallback mapping
    # This is the CRITICAL path when catalog file doesn't exist.
    static_slug = _STATIC_MODEL_MAP.get(query)
    if static_slug:
        logger.debug(
            f"Model '{marketing_name}' resolved via static fallback → {static_slug}"
        )
        return static_slug

    return None
