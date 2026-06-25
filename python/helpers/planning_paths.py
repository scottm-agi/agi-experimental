"""Single source of truth for all planning file locations.

Every planning/phase output file in AGIX should be referenced through
this module. All paths are under the project's docs/ subdirectory.

Usage:
    from python.helpers.planning_paths import PLANNING_PATHS, get_path

    # Get canonical path for a planning artifact
    ledger = get_path(project_dir, "requirements_ledger")
    # => "/path/to/project/docs/requirements-ledger.json"

Created as part of Gap-6 fix: planning files were scattered between
project root and docs/. This module consolidates them all into docs/.
"""
from __future__ import annotations

import os

# ═══════════════════════════════════════════════════════════════
# Canonical planning file locations — ALL under docs/
# ═══════════════════════════════════════════════════════════════
#
# Key naming convention: snake_case matching the conceptual artifact name
# Path naming convention: hyphen-case matching existing docs/ convention
#
# Phase 0:   requirements extraction outputs
# Phase 0.5: research outputs
# Phase 2:   architecture + BDD outputs
# Phase 2.3: design outputs
# Phase 2.6: cross-check outputs
# Phase 2.7+: TDD outputs

PLANNING_PATHS: dict[str, str] = {
    # Phase 0: Requirements
    "requirements_ledger": "docs/requirements-ledger.json",
    "content_manifest": "docs/content-manifest.json",

    # Phase 0.5: Research
    "framework_research": "docs/framework-research.md",

    # Phase 2: Architecture
    "architect_plan": "docs/architect-plan.json",
    "architecture_spec": "docs/architecture-spec.md",
    "bdd_scenarios": "docs/bdd-scenarios.md",
    "decomposition_index": "docs/decomposition-index.json",

    # Phase 2.3: Design
    "design_tokens": "docs/design-tokens.json",
    "component_spec": "docs/component-spec.md",
    "ux_flows": "docs/ux-flows.md",
    "design_mockups": "docs/design-mockups/",

    # Phase 2.6: Cross-check
    "planning_cross_check": "docs/planning-cross-check.md",
    "design_cross_check": "docs/design-cross-check.md",

    # Phase 2.7-2.8: TDD
    "test_skeleton": "docs/test-skeleton.json",
    "tdd_dir": "docs/tdd/",
}


def get_path(project_dir: str, key: str) -> str:
    """Get the canonical absolute path for a planning artifact.

    Args:
        project_dir: Root directory of the project.
        key: Artifact key from PLANNING_PATHS (e.g., "requirements_ledger").

    Returns:
        Absolute path to the artifact (e.g., "/path/to/project/docs/requirements-ledger.json").

    Raises:
        KeyError: If the key is not in PLANNING_PATHS.
    """
    return os.path.join(project_dir, PLANNING_PATHS[key])
