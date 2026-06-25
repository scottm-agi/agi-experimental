"""
Validators Package — Modular framework-specific quality gates.

Provides per-framework validation modules that the orchestrator completion
gate imports selectively based on detected/declared project frameworks.

Modules:
    common        — Framework-agnostic checks (deliverables, test coverage, sitemap)
    node          — Node.js / React / Next.js checks (boilerplate, npm, build, landing page)
    tailwind      — Tailwind CSS config validation
    lit           — Logical Integration Testing (LIT) plan validation and enforcement
    bdd_scenarios — BDD acceptance scenario parser & validator (Gherkin → static analysis)

Usage:
    from python.helpers.validators import run_validators
    from python.helpers.validators.node import check_boilerplate
    from python.helpers.validators.tailwind import check_tailwind_config
    from python.helpers.validators.lit import (
        check_lit_plan_exists, validate_lit_plan_structure,
        detect_lit_execution_evidence, generate_lit_plan_from_sitemap,
    )
    from python.helpers.validators.bdd_scenarios import (
        parse_bdd_scenarios, validate_bdd_scenarios,
    )
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("agix.validators")


# ─── Shared Constants ─────────────────────────────────────────────────

# Orchestrator agents that should be guarded
ORCHESTRATOR_AGENTS = {"multiagentdev", "alex", "default"}

# Minimum delegations before response allowed (fallback when no task list)
MIN_DELEGATION_COUNT = 1

# Maximum consecutive blocks before safety valve
MAX_COMPLETION_BLOCKS = 6

# Keywords that indicate subordinate performed real E2E verification
VERIFICATION_EVIDENCE_KEYWORDS = [
    "npm run build",
    "next build",
    "build succeeded",
    "compiled successfully",
    "scrape_url",
    "curl ",
    "curl(",
    "http://localhost",
    "https://localhost",
    "renders correctly",
    "page renders",
    "returned 200",
    "status 200",
    "HTTP_STATUS:",
    "health check passed",
    "browser verification",
    "screenshot",
    "E2E verification",
    "no boilerplate",
    "boilerplate detected",
]

# Keywords indicating a quality audit was performed on UI/UX (RC-30)
QUALITY_AUDIT_KEYWORDS = [
    "quality audit",
    "design quality",
    "visual hierarchy",
    "ui/ux",
    "uat specialist",
    "quality assessment",
    "typography consistency",
    "color harmony",
]

# Boilerplate indicators — text that signals agent left default scaffold
BOILERPLATE_INDICATORS = [
    "to get started, edit the page",
    "scaffold-temp",
    "temp-scaffold",
    "welcome to next.js",
    "get started by editing",
    "edit src/app/page.tsx",
    "powered by next.js",
    "create-next-app",
    "my-app",
]

# Generic page/route file names (framework-agnostic)
PAGE_FILE_BASENAMES = {
    "page", "index", "route", "+page", "+layout",
    "_app", "_document", "layout", "default",
}

# Extensions to scan for deliverable files
DELIVERABLE_EXTENSIONS = {
    ".tsx", ".jsx", ".ts", ".js",
    ".vue", ".svelte",
    ".py", ".rb", ".php",
    ".html", ".astro",
}

# Test file naming patterns (framework-agnostic)
TEST_FILE_PATTERNS = {
    ".test.", ".spec.", "_test.", "_spec.",
    "test_", "spec_",
}

# Common test directory names
TEST_DIR_NAMES = {
    "__tests__", "tests", "test", "spec", "specs",
    "__test__", "e2e", "integration",
}



