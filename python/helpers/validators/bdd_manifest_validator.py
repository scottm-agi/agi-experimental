"""BDD ↔ Manifest Consistency Validator.

RCA-475 GAP-3: Standalone validator that checks BDD scenario feature names
reference actual manifest entries. Wraps logic from gates 2.02/2.06 into a
reusable function that can be run as a pre-code TDD test.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.validators.bdd_manifest")


def validate_bdd_manifest_consistency(
    bdd_text: str,
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Check BDD scenarios reference real manifest entries.

    Cross-references BDD scenario feature/page names against manifest
    page names and feature names. Identifies:
      - orphaned_scenarios: BDD scenarios with no manifest match
      - missing_coverage: manifest entries with no BDD scenario

    Args:
        bdd_text: Raw BDD scenario text (markdown).
        manifest: Parsed content_manifest.json dict.

    Returns:
        Dict with ``consistent`` (bool), ``orphaned_scenarios`` (list),
        ``missing_coverage`` (list), and ``total_scenarios`` (int).
    """
    if not bdd_text or not manifest:
        return {
            "consistent": True,
            "orphaned_scenarios": [],
            "missing_coverage": [],
            "total_scenarios": 0,
        }

    # ── Extract BDD scenario names ──
    # Pattern: "Scenario: <name>" or "Feature: <name>" lines
    scenario_pattern = re.compile(
        r"^\s*(?:Scenario|Feature):\s*(.+)$", re.MULTILINE | re.IGNORECASE
    )
    scenario_names = [m.group(1).strip() for m in scenario_pattern.finditer(bdd_text)]

    if not scenario_names:
        return {
            "consistent": True,
            "orphaned_scenarios": [],
            "missing_coverage": [],
            "total_scenarios": 0,
        }

    # ── Extract manifest entry names ──
    manifest_names = set()

    # Pages
    for page in manifest.get("pages", []):
        name = page.get("name", "") or page.get("title", "")
        if name:
            manifest_names.add(name.lower().strip())

    # Features / sections
    for feature in manifest.get("features", []):
        name = feature.get("name", "") or feature.get("title", "")
        if name:
            manifest_names.add(name.lower().strip())

    for section in manifest.get("sections", []):
        name = section.get("name", "") or section.get("title", "")
        if name:
            manifest_names.add(name.lower().strip())

    # Requirements (if present)
    for req in manifest.get("requirements", []):
        text = req.get("text", "")
        if text:
            manifest_names.add(text.lower().strip()[:80])

    if not manifest_names:
        # No manifest entries to cross-reference
        return {
            "consistent": True,
            "orphaned_scenarios": [],
            "missing_coverage": [],
            "total_scenarios": len(scenario_names),
        }

    # ── Cross-reference ──
    orphaned = []
    matched_manifest = set()

    for scenario in scenario_names:
        scenario_lower = scenario.lower()
        found = False
        for mname in manifest_names:
            # Fuzzy match: manifest name appears in scenario name or vice versa
            if mname in scenario_lower or scenario_lower in mname:
                matched_manifest.add(mname)
                found = True
                break
            # Word overlap: at least 2 significant words overlap
            s_words = set(w for w in scenario_lower.split() if len(w) >= 3)
            m_words = set(w for w in mname.split() if len(w) >= 3)
            if len(s_words & m_words) >= 2:
                matched_manifest.add(mname)
                found = True
                break
        if not found:
            orphaned.append(scenario)

    missing = [
        name for name in manifest_names
        if name not in matched_manifest
    ]

    return {
        "consistent": len(orphaned) == 0 and len(missing) == 0,
        "orphaned_scenarios": orphaned,
        "missing_coverage": sorted(missing),
        "total_scenarios": len(scenario_names),
    }
