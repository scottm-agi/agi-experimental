"""
BDD Scenario Generation Module

Extracted from skeleton_generator.py as part of P0-3 decomposition.
Handles all BDD (Behavior-Driven Development) related functionality:
  - BDD skeleton generation from requirements ledger
  - BDD coverage gate (REQ-ID presence check)
  - BDD content coverage (noun overlap + boilerplate detection)
  - BDD error-path coverage (integration requirements)
  - BDD price cross-reference validation
  - BDD literal cross-checker
  - BDD behavioral consistency (manifest routing inversions)
  - BDD REQ-ID enforcement (deterministic injection)
  - BDD structured tool validation + assembly
  - Feature sub-type classification
  - Compliance sub-type classification
  - Feature file generation (.feature Gherkin files)
  - Scenario manifest generation (YAML)

Architecture:
  - This module provides the BDD layer of the requirements pipeline
  - Consumed by: requirements tool (Phase 2 gate), orchestrator, architect
  - Imports shared utilities from manifest_parser.py and skeleton_generator.py
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from python.helpers.gate_config import BDD_COVERAGE_THRESHOLD

logger = logging.getLogger("agix.bdd_generator")



# Re-exports
from python.helpers.bdd_generator_constants import (
    _CATEGORY_THEN_CLAUSES,
    _FEATURE_SUBTYPE_PATTERNS,
    _COMPLIANCE_SUBTYPE_PATTERNS,
    _BDD_CATEGORIES,
    _ERROR_PATH_KEYWORDS,
    _HAPPY_TRIGGERS,
    _UNHAPPY_TRIGGERS,
    _PUBLIC_DESTINATIONS,
    _PRIVATE_DESTINATIONS,
    _SCENARIO_BLOCK_RE,
    _THEN_LINE_RE,
    _WHEN_LINE_RE,
    _STOP_WORDS,
    _BOILERPLATE_PATTERNS,
)
# Re-exports (moved to end to prevent circular imports of constants)
from python.helpers.bdd_generator_helpers import _classify_feature_subtype, _classify_compliance_subtype, _resolve_category, _classify_sentiment, _classify_destination, _extract_manifest_conditions, _extract_key_nouns, _extract_bdd_steps, _is_boilerplate_scenario
from python.helpers.bdd_generator_creation import generate_bdd_skeleton, generate_feature_files, generate_scenario_manifest, assemble_bdd_from_structured, auto_correct_bdd_literals
from python.helpers.bdd_generator_validation import check_bdd_coverage, check_bdd_error_paths, validate_bdd_prices, enforce_bdd_req_traceability, validate_bdd_scenario_input, validate_bdd_literals, validate_bdd_behavioral_consistency, validate_bdd_conditional_completeness, check_bdd_content_coverage, _semantic_validate_bdd_routing
