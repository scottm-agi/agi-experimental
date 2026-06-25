"""
F-1: Page Coverage Validation Gate.

Verifies that every page/route listed in docs/architecture.md has a
corresponding Phase 3.x work package in decomposition_index.json.

Problem: Decomposition creates backend API phases but no phase targets
frontend pages. Dashboard was left as a stub ('coming in Phase 3...').

2-Layer Detection (ADR-085):
  L1: Deterministic regex/keyword — parse Page Map, exact match page
      names against work package descriptions.
  L1.5: Semantic embedding — for pages without exact L1 matches, compare
      embeddings using compute_embedding_sync() + cosine_similarity().
      Match threshold: 0.6
  L2: Not needed — L1+L1.5 are sufficient for page matching.

Gate action: Block with specific message listing unmatched pages.
"""
import os
import re
import json
import logging
from typing import Dict, List, Optional, Tuple

from python.helpers.orchestrator_gate_integration_checks import register_check
from python.helpers.planning_paths import get_path as _planning_path

logger = logging.getLogger("agix.checks.page_coverage")

# ── Semantic embedding imports (ADR-085) ──
try:
    from python.helpers.semantic_embeddings import (
        compute_embedding_sync,
        cosine_similarity,
    )
except ImportError:
    # Fail open — if embeddings aren't available, L1 only
    compute_embedding_sync = None  # type: ignore[assignment]
    cosine_similarity = None  # type: ignore[assignment]

# ── Constants ──
SEMANTIC_MATCH_THRESHOLD = 0.6
ARCHITECTURE_FILE_CANDIDATES = [
    os.path.join("docs", "architecture.md"),
    os.path.join(".agix.proj", "architecture.md"),
    "architecture.md",
]
# First candidate uses planning_paths canonical location (docs/decomposition-index.json),
# second candidate is the legacy .agix.proj fallback.
_DECOMP_LEGACY_FALLBACK = os.path.join(".agix.proj", "decomposition_index.json")
_DECOMP_LEGACY_FALLBACK_HYPHEN = os.path.join(".agix.proj", "decomposition-index.json")


# ─── L1: Parse architecture.md for page/route entries ───────────────────

# Patterns to extract routes from Page Map section:
# - `/route` → Label  or  - `/route` -> Label
# | `/route` | Label |
_ROUTE_PATTERNS = [
    re.compile(
        r'-\s*`?(/[a-zA-Z0-9_\[\]/\-]*)`?\s*(?:→|->|—|:)\s*(.+)',
        re.IGNORECASE,
    ),
    re.compile(
        r'\|\s*`?(/[a-zA-Z0-9_\[\]/\-]*)`?\s*\|\s*([^|]+)\s*\|',
        re.IGNORECASE,
    ),
]


def _extract_pages_from_architecture(arch_text: str) -> List[Dict[str, str]]:
    """Extract page/route entries from architecture.md Page Map section.

    Returns list of dicts: [{"route": "/dashboard", "label": "Dashboard"}, ...]
    """
    if not arch_text:
        return []

    # Find the Page Map section
    page_map_match = re.search(
        r'(?:^|\n)#{1,3}\s*Page\s+Map\b',
        arch_text,
        re.IGNORECASE,
    )
    if not page_map_match:
        return []

    # Extract text from Page Map to next section header
    rest = arch_text[page_map_match.start():]
    next_section = re.search(r'\n#{1,3}\s+(?!Page\s+Map)', rest[10:])
    if next_section:
        page_map_text = rest[:next_section.start() + 10]
    else:
        page_map_text = rest

    # Extract routes from the page map section
    pages = []
    seen_routes = set()
    for pattern in _ROUTE_PATTERNS:
        for match in pattern.finditer(page_map_text):
            route = match.group(1).strip()
            label = match.group(2).strip().rstrip("|").strip()
            if route and route not in seen_routes:
                seen_routes.add(route)
                pages.append({"route": route, "label": label})

    return pages


# ─── L1: Parse decomposition_index.json for Phase 3 work packages ──────

def _load_work_packages(project_dir: str) -> Optional[List[Dict[str, str]]]:
    """Load Phase 3 work packages from decomposition_index.json.

    Returns list of dicts: [{"id": "3.1", "description": "..."}, ...]
    Returns None if file not found or unparseable.
    """
    decomp_candidates = [
        _planning_path(project_dir, "decomposition_index"),
        os.path.join(project_dir, _DECOMP_LEGACY_FALLBACK),
        os.path.join(project_dir, _DECOMP_LEGACY_FALLBACK_HYPHEN),
    ]
    for path in decomp_candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Support multiple decomp index formats
                phases = data.get("phases", {})

                # Look for Phase 3.x work packages across all phases
                work_packages = []
                for phase_key, phase_data in phases.items():
                    if str(phase_key).startswith("3"):
                        wps = phase_data.get("work_packages", [])
                        work_packages.extend(wps)

                return work_packages
            except (json.JSONDecodeError, IOError, OSError, AttributeError):
                continue
    return None


def _load_architecture_text(project_dir: str) -> Optional[str]:
    """Load architecture.md text from project directory."""
    for candidate in ARCHITECTURE_FILE_CANDIDATES:
        path = os.path.join(project_dir, candidate)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except (IOError, OSError):
                continue
    return None


# ─── L1: Exact string match ────────────────────────────────────────────

def _l1_exact_match(page: Dict[str, str], work_packages: List[Dict[str, str]]) -> bool:
    """L1 deterministic check: does any work package description contain the page label?

    Case-insensitive substring match on both the route name and label.
    """
    label_lower = page["label"].lower()
    route_lower = page["route"].lower().strip("/").replace("-", " ").replace("_", " ")

    for wp in work_packages:
        desc_lower = wp.get("description", "").lower()
        # Match by label (e.g., "Dashboard" in "Build Dashboard page")
        if label_lower and label_lower in desc_lower:
            return True
        # Match by route (e.g., "dashboard" in "Build dashboard page")
        if route_lower and route_lower in desc_lower:
            return True
    return False


# ─── L1.5: Semantic embedding match (ADR-085) ──────────────────────────

def _l15_semantic_match(
    page: Dict[str, str],
    work_packages: List[Dict[str, str]],
) -> bool:
    """L1.5 semantic check: embed page description and work package descriptions.

    Uses all-MiniLM-L6-v2 in-memory model via semantic_embeddings.py.
    Returns True if any work package has cosine_similarity > SEMANTIC_MATCH_THRESHOLD.
    Fails open: returns False (unmatched) if embeddings unavailable.
    """
    if compute_embedding_sync is None or cosine_similarity is None:
        return False  # Fail open — rely on L1

    # Build page description for embedding
    page_desc = f"{page['label']} page at {page['route']}"
    page_embedding = compute_embedding_sync(page_desc)
    if page_embedding is None:
        return False  # Fail open

    for wp in work_packages:
        wp_desc = wp.get("description", "")
        if not wp_desc:
            continue
        wp_embedding = compute_embedding_sync(wp_desc)
        if wp_embedding is None:
            continue
        similarity = cosine_similarity(page_embedding, wp_embedding)
        if similarity > SEMANTIC_MATCH_THRESHOLD:
            logger.debug(
                f"[PAGE COVERAGE] L1.5 semantic match: "
                f"'{page['label']}' ↔ '{wp_desc}' (similarity={similarity:.3f})"
            )
            return True

    return False


# ─── Main check function ───────────────────────────────────────────────

def check_page_coverage(ctx) -> Optional[str]:
    """F-1: Page Coverage Validation Gate.

    Verifies every page in architecture.md has a matching Phase 3.x
    work package in decomposition_index.json.

    Args:
        ctx: CheckContext with project_dir, block() method.

    Returns:
        None if check passes, block message string if it fails.
    """
    try:
        if not ctx.project_dir:
            return None

        # Load architecture pages
        arch_text = _load_architecture_text(ctx.project_dir)
        if not arch_text:
            return None  # No architecture file → skip

        pages = _extract_pages_from_architecture(arch_text)
        if not pages:
            return None  # No Page Map section → skip

        # Load decomposition work packages
        work_packages = _load_work_packages(ctx.project_dir)
        if work_packages is None:
            return None  # No decomp file → skip

        # Check each page for a matching work package
        unmatched = []
        for page in pages:
            # L1: Exact string match
            if _l1_exact_match(page, work_packages):
                continue

            # L1.5: Semantic embedding match
            if _l15_semantic_match(page, work_packages):
                continue

            # No match found — this page has no work package
            unmatched.append(page)

        if not unmatched:
            return None  # All pages covered

        # Build block message listing unmatched pages
        page_list = ", ".join(
            f"`{p['route']}` ({p['label']})" for p in unmatched
        )
        return ctx.block(
            f"⚠️ PAGE COVERAGE GAP: {len(unmatched)} page(s) in architecture.md "
            f"have NO matching Phase 3 work package in decomposition_index.json: "
            f"{page_list}. Add work packages targeting these pages to ensure "
            f"they are implemented. Without a work package, these pages will "
            f"ship as stubs or be skipped entirely.",
            action=(
                f"Add Phase 3 work packages for unmatched pages: {page_list}. "
                f"Each page in the Page Map MUST have a dedicated work package."
            ),
        )
    except Exception as e:
        logger.debug(f"[PAGE COVERAGE] Check skipped due to error: {e}")
        return None  # Fail open


# ─── Gate Registration ─────────────────────────────────────────────────
# F-1: Registered as a blocking check at order 2.07 (after BDD checks).
# Changed from 2.06 to avoid collision with BDD literal consistency.

@register_check(2.07, "Page coverage validation", critical=False, web_only=True, gate="tdd")
def _check_page_coverage(ctx):
    """Gate-registered wrapper for check_page_coverage."""
    return check_page_coverage(ctx)
