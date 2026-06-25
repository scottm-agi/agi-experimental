"""Aggregator Hallucination Detection — Standalone Module.

Detects fabricated PII (names, profiles, contact info) in output from
LLM-based search aggregators (Perplexity, Tavily, etc.).

This module is deliberately isolated from the heavy SearchEngine class
so it can be imported and tested without duckduckgo_search and other deps.

Usage:
    from python.tools.aggregator_fidelity import flag_aggregator_hallucination
    flagged = flag_aggregator_hallucination(perplexity_output)
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger("aggregator-fidelity")


# ==============================================================================
# Hallucination Signal Patterns
# ==============================================================================

# Patterns that indicate fabricated PII from LLM-based search aggregators
HALLUCINATION_SIGNALS = [
    re.compile(r'verified\s+profiles?', re.IGNORECASE),
    re.compile(r'key\s+examples?\s*\(', re.IGNORECASE),
    re.compile(r'\|\s*\*{0,2}name\*{0,2}\s*\|', re.IGNORECASE),  # | Name | or | **Name** |
]

# LinkedIn search-query URLs masquerading as profile links
LINKEDIN_SEARCH_URL = re.compile(
    r'linkedin\.com/search/results/', re.IGNORECASE
)

# Markdown table with a name-like column (| Name | ... | or | **Name** | ... |)
TABLE_WITH_NAMES = re.compile(
    r'\|[^|]*(?:name|employee|person|contact)[^|]*\|.*\|',
    re.IGNORECASE | re.MULTILINE
)

VERIFICATION_WARNING = (
    "\n\n⚠️ AGGREGATOR VERIFICATION REQUIRED:\n"
    "The above results may contain AI-generated/fabricated data (names, profiles, URLs).\n"
    "Aggregator LLMs (Perplexity, etc.) can hallucinate specific names and construct fake URLs.\n"
    "\n"
    "MANDATORY before including ANY names or profiles in your response:\n"
    "1. VERIFY each name by calling `scrape_url` on the cited source URL\n"
    "2. If no source URL exists for a specific name → DROP IT (likely fabricated)\n"
    "3. LinkedIn search-query URLs (/search/results/...) are NOT profile URLs — they are constructed\n"
    "4. Use `scrape_url` on authoritative sources (layoffs.fyi, news articles, WARN databases) instead\n"
    "5. NEVER include unverified names in your final response\n"
)


def flag_aggregator_hallucination(text: str) -> str:
    """Scan aggregator output for hallucination signals and append warning if found.

    Returns the original text (unchanged) if clean, or text + warning if suspicious.
    """
    if not text or len(text) < 50:
        return text

    signals_found = []

    # Check 1: LinkedIn search-query URLs (strongest signal)
    linkedin_search_matches = LINKEDIN_SEARCH_URL.findall(text)
    if linkedin_search_matches:
        signals_found.append(f"LinkedIn search URLs ({len(linkedin_search_matches)} found)")

    # Check 2: "Verified Profiles" / "Key Examples" synthetic headers
    for pattern in HALLUCINATION_SIGNALS:
        if pattern.search(text):
            signals_found.append(f"Suspicious header: {pattern.pattern}")

    # Check 3: Markdown table with name-like column + at least one row
    if TABLE_WITH_NAMES.search(text):
        # Count table rows (lines starting with |)
        table_rows = [line for line in text.split('\n') if line.strip().startswith('|')]
        # Need both header + separator + at least 1 data row = 3+ lines
        if len(table_rows) >= 3:
            signals_found.append(f"Table with name column ({len(table_rows)} rows)")

    if signals_found:
        logger.warning(
            f"[AGGREGATOR FIDELITY] Hallucination signals detected in search output: "
            f"{', '.join(signals_found)}"
        )
        return text + VERIFICATION_WARNING

    return text
