"""
Prompt Contract Parser — Deterministic, NO-LLM extraction of verifiable
assertions from raw user prompts.

RCA-244: Extracts literal values (URLs, prices, model names, emails, person
names) as immutable assertions in a JSON contract. Each assertion can be
verified by a simple grep against the generated source code.

All extraction uses regex patterns — no LLM calls, sub-second execution.

This module serves as the **facade** for the prompt contract parsing system.
Implementation is split across:
  - contract_text_utils.py — text processing helpers
  - contract_extractors.py — extraction functions

All public functions are re-exported here so existing imports continue to work.

Usage:
    from python.helpers.prompt_contract_parser import build_contract

    contract = build_contract(prompt_text)
    # Returns: {"contract_version": "1.0", "assertions": [...], ...}
"""

import hashlib
import logging
import os
import re
from typing import Dict, List, Set

# ─── Re-exports from sub-modules ─────────────────────────────────────
# All public functions are re-exported so that existing imports like
#   from python.helpers.prompt_contract_parser import extract_assertions
# continue to work unchanged.

from python.helpers.contract_text_utils import (  # noqa: F401
    _normalize_prompt_text,
    _clean_url,
    _is_valid_person_name,
    _detect_section_context,
    _compute_price_confidence,
    _name_to_route,
    _classify_feature_category,
)

from python.helpers.contract_extractors import (  # noqa: F401
    extract_assertions,
    _dedup_url_assertions,
    extract_features,
    extract_behaviors,
    extract_env_vars,
    extract_compliance_requirements,
    extract_user_journeys,
    extract_checklist_items,
    infer_implied_features,
)

logger = logging.getLogger("agix.prompt_contract_parser")


# ─── Contract Builder ─────────────────────────────────────────────────


def build_contract(prompt: str) -> dict:
    """Build a full requirements contract JSON from a raw prompt.

    Returns:
        {
            "contract_version": "2.0",
            "source_prompt_hash": "sha256:...",
            "assertions": [...],
            "features": [...],
            "behaviors": [...],
            "env_vars": [...],
            "bdd_content_clauses": [...],
            "journeys": [...]
        }
    """
    prompt_hash = hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()
    assertions = extract_assertions(prompt)
    features = extract_features(prompt)
    behaviors = extract_behaviors(prompt)
    env_vars = extract_env_vars(prompt)
    compliance = extract_compliance_requirements(prompt)
    journeys = extract_user_journeys(prompt)

    # U-4: Append compliance requirements to assertions list.
    # Compliance items have type="compliance" and confidence=1.0,
    # so they flow through the existing run_contract_assertions()
    # pipeline and are never skipped by confidence thresholds.
    for cr in compliance:
        assertions.append({
            "id": f"COMPLIANCE-{len(assertions) + 1:03d}",
            "type": "compliance",
            "value": cr["verify_pattern"],
            "immutable": True,
            "category": "compliance",
            "confidence": 1.0,
            "name": cr["name"],
            "framework": cr["framework"],
            "hard_requirement": True,
            "verify_pattern": cr["verify_pattern"],
        })

    # FIX-14: Extract checklist items as mandatory requirements.
    # Every checkbox item (⬜/☑/☐/[x]/[ ]) becomes a hard_requirement assertion.
    checklist_items = extract_checklist_items(prompt)
    for ci in checklist_items:
        assertions.append({
            "id": f"CHECKLIST-{len(assertions) + 1:03d}",
            "type": "checklist",
            "value": ci["name"],
            "immutable": True,
            "category": "checklist",
            "confidence": 1.0,
            "name": ci["name"],
            "hard_requirement": True,
        })

    # FIX-13: Infer implied features from extracted feature patterns.
    # E.g., dashboard → auth, email → unsubscribe, payments → pricing.
    implied = infer_implied_features(features, compliance)
    features.extend(implied)

    # Generate BDD content clauses for each assertion
    bdd_clauses = []
    for a in assertions:
        if a["type"] in ("url", "price", "model_name", "email", "person_name"):
            bdd_clauses.append(f'Then page contains "{a["value"]}"')
        elif a["type"] == "compliance":
            bdd_clauses.append(
                f'Then code satisfies compliance: {a.get("name", a["value"])}'
            )
        elif a["type"] == "checklist":
            bdd_clauses.append(
                f'Then code implements checklist item: {a.get("name", a["value"])}'
            )

    return {
        "contract_version": "2.0",
        "source_prompt_hash": f"sha256:{prompt_hash}",
        "assertions": assertions,
        "features": features,
        "behaviors": behaviors,
        "env_vars": env_vars,
        "bdd_content_clauses": bdd_clauses,
        "journeys": journeys,
    }


# ─── F-4 (ITR-11): URL Assertion Source Verification ─────────────────────
# Verifies that URL assertions extracted from the prompt contract actually
# appear in the project source code. A URL in the contract but missing from
# source means the code agent never wired it.

# DUP-5: Import from canonical source (contract_patterns.py) instead of
# duplicating the definitions here.
from python.helpers.contract_patterns import (  # noqa: F401
    _URL_VERIFY_EXTENSIONS,
    _URL_VERIFY_SKIP_DIRS,
)
from python.helpers.source_scanner import read_project_files


def verify_url_assertions_in_source(
    assertions: List[dict],
    project_src_dir: str,
) -> List[Dict]:
    """Verify that URL assertions from the prompt contract exist in source code.

    Filters to URL type assertions only, then recursively searches all files
    in project_src_dir for each URL value string.

    Args:
        assertions: List of assertion dicts (from extract_assertions()).
            Each must have 'id', 'type', and 'value' keys.
        project_src_dir: Path to the project source directory to search.

    Returns:
        List of result dicts, one per URL assertion, each with:
            assertion_id: str — the assertion's ID
            url: str — the URL value
            found_in_source: bool — whether the URL was found
            matched_files: List[str] — relative paths of files containing the URL
    """
    # Filter to URL-type assertions only
    url_assertions = [a for a in assertions if a.get('type') == 'url']
    if not url_assertions:
        return []

    # Pre-read all searchable files (once) for efficiency
    # OVL-3: Use centralized scanner instead of inline os.walk
    file_contents: Dict[str, str] = {}  # rel_path → content
    src_exists = os.path.isdir(project_src_dir)

    if src_exists:
        file_contents = read_project_files(
            project_src_dir,
            extensions=_URL_VERIFY_EXTENSIONS,
            skip_dirs=_URL_VERIFY_SKIP_DIRS,
        )

    results: List[Dict] = []
    for assertion in url_assertions:
        assertion_id = assertion.get('id', '')
        url_value = assertion.get('value', '')

        matched_files: List[str] = []
        for rel_path, content in file_contents.items():
            if url_value in content:
                matched_files.append(rel_path)

        results.append({
            'assertion_id': assertion_id,
            'url': url_value,
            'found_in_source': len(matched_files) > 0,
            'matched_files': matched_files,
        })

    return results
