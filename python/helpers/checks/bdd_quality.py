"""BDD and Requirements Quality — advisory + blocking checks.

Blocking checks for BDD scenario quality (price fidelity, content coverage,
requirements completeness). Advisory checks for error-path coverage and README.

FIX-4: BDD price fidelity is a BLOCKING check via @register_check.
GAP-5: BDD content coverage (2.03) and Requirements completeness (2.04)
       PROMOTED from advisory to blocking via @register_check.
       Root cause: these checks ran as advisory (ctx.block() was a no-op in
       advisory mode), so agents could deliver with low BDD coverage or
       incomplete requirements — the gate logged warnings but never blocked.

BDD error paths (2.01) and README (2.05) remain advisory — they are either
not universally applicable or not mechanically fixable by the agent.
"""
import os
import json
import logging

from typing import Optional

from python.helpers.orchestrator_gate_integration_checks import register_advisory, register_check
from python.helpers.planning_paths import get_path as _planning_path

logger = logging.getLogger("agix.checks.bdd_quality")


# FIX-10: Canonical BDD file locations
BDD_FILE_CANDIDATES = [
    os.path.join(".agix.proj", "bdd_scenarios.md"),
    "bdd_scenarios.md",
    os.path.join("docs", "bdd-scenarios.md"),
]
BDD_PRIMARY_PATH = os.path.join("docs", "bdd-scenarios.md")


# ── Helper: locate BDD scenarios file ────────────────────────────────────
def _find_bdd_text(project_dir: str) -> Optional[str]:
    """Return BDD scenario text from the project, or None if not found."""
    for candidate in BDD_FILE_CANDIDATES:
        path = os.path.join(project_dir, candidate)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except (IOError, OSError):
                continue
    return None


# ── 1. BDD Error Paths ──────────────────────────────────────────────────

@register_advisory(2.01, "BDD error paths", web_only=True)
def _advisory_bdd_error_paths(ctx):
    """Check integration requirements have error-path BDD scenarios."""
    try:
        from python.helpers.skeleton_generator import (
            check_bdd_error_paths,
            generate_test_skeleton,
        )

        if not ctx.project_dir:
            return None

        skeleton = generate_test_skeleton(ctx.project_dir)
        skeleton_reqs = skeleton.get("requirements", [])
        if not skeleton_reqs:
            return None

        bdd_text = _find_bdd_text(ctx.project_dir)
        if not bdd_text:
            return None

        result = check_bdd_error_paths(skeleton_reqs, bdd_text)
        if result["missing"]:
            count = len(result["missing"])
            ids = ", ".join(result["missing"][:5])
            return ctx.block(
                f"⚠️ BDD ERROR PATHS: {count} integration requirement(s) lack "
                f"error-path BDD scenarios: {ids}"
            )
        return None
    except Exception as e:
        logger.debug(f"[BDD ERROR PATHS] Advisory skipped: {e}")
        return None


# ── 2. BDD Price Fidelity ────────────────────────────────────────────────

@register_check(2.02, "BDD price fidelity", critical=True, web_only=True, gate="bdd")
def _blocking_bdd_prices(ctx):
    """Cross-reference prices in BDD text against manifest."""
    try:
        from python.helpers.skeleton_generator import validate_bdd_prices

        if not ctx.project_dir:
            return None

        manifest_path = _planning_path(ctx.project_dir, "content_manifest")
        if not os.path.isfile(manifest_path):
            return None

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            return None

        bdd_text = _find_bdd_text(ctx.project_dir)
        if not bdd_text:
            return None

        result = validate_bdd_prices(bdd_text, manifest)
        if result["mismatches"]:
            prices = ", ".join(m["bdd_price"] for m in result["mismatches"][:5])
            return ctx.block(
                f"⚠️ BDD PRICE MISMATCH: {len(result['mismatches'])} price(s) in "
                f"BDD scenarios don't match manifest: {prices}"
            )
        return None
    except Exception as e:
        logger.debug(f"[BDD PRICE FIDELITY] Advisory skipped: {e}")
        return None


# ── 3. BDD Content Coverage ─────────────────────────────────────────────
# GAP-5: Promoted from @register_advisory → @register_check.
# Root cause: advisory ctx.block() is a no-op, so low BDD coverage never
# blocked delivery. Agents delivered with 0% BDD coverage unchecked.

@register_check(2.03, "BDD content coverage", critical=True, web_only=True, gate="bdd")
def _blocking_bdd_content_coverage(ctx):
    """Check BDD scenario content quality, not just REQ-ID presence."""
    try:
        from python.helpers.skeleton_generator import (
            check_bdd_content_coverage,
            generate_test_skeleton,
        )

        if not ctx.project_dir:
            return None

        skeleton = generate_test_skeleton(ctx.project_dir)
        skeleton_reqs = skeleton.get("requirements", [])
        if not skeleton_reqs:
            return None

        bdd_text = _find_bdd_text(ctx.project_dir)
        if not bdd_text:
            return None

        result = check_bdd_content_coverage(skeleton_reqs, bdd_text)
        gaps = result.get("gaps", [])
        shallow = result.get("shallow_scenarios", [])
        coverage_pct = result.get("coverage_pct", 1.0)

        if (gaps or shallow) and coverage_pct < 0.5:
            details = []
            if gaps:
                details.append(f"{len(gaps)} gap(s): {', '.join(gaps[:3])}")
            if shallow:
                details.append(f"{len(shallow)} shallow: {', '.join(shallow[:3])}")
            return ctx.block(
                f"⚠️ BDD CONTENT COVERAGE LOW ({coverage_pct:.0%}): "
                f"{'; '.join(details)}"
            )
        return None
    except Exception as e:
        logger.debug(f"[BDD CONTENT COVERAGE] Advisory skipped: {e}")
        return None


# ── 4. Requirements Completeness ─────────────────────────────────────────
# GAP-5: Promoted from @register_advisory → @register_check.
# Root cause: advisory ctx.block() is a no-op, so pending requirements
# never blocked delivery. Agents could mark "done" with requirements still
# pending in the ledger.

@register_check(2.04, "Requirements completeness", critical=True, gate="bdd")
def _blocking_requirements_completeness(ctx):
    """Check all requirements in ledger are completed."""
    try:
        from python.helpers.orchestrator_gate_common import (
            check_requirements_completeness,
        )

        if not ctx.project_dir:
            return None

        result = check_requirements_completeness(ctx.project_dir)
        pending = result.get("pending", [])
        if pending:
            ids = ", ".join(p["id"] for p in pending[:5])
            return ctx.block(
                f"⚠️ REQUIREMENTS INCOMPLETE: {len(pending)} requirement(s) still "
                f"pending: {ids}"
            )
        return None
    except Exception as e:
        logger.debug(f"[REQUIREMENTS COMPLETENESS] Advisory skipped: {e}")
        return None


# ── 5. Project README ────────────────────────────────────────────────────

# Boilerplate markers that indicate a scaffold/template README
_BOILERPLATE_MARKERS = [
    "Create Next App",
    "bootstrapped with",
    "Geist",
    "Vercel",
]


@register_advisory(2.05, "Project README", web_only=True)
def _advisory_project_readme(ctx):
    """Check project has a non-boilerplate README."""
    try:
        if not ctx.project_dir:
            return None

        readme_path = os.path.join(ctx.project_dir, "README.md")
        if not os.path.isfile(readme_path):
            return ctx.block(
                "⚠️ NO PROJECT README: README.md is missing. Use "
                "generate_project_readme() to create project-specific documentation."
            )

        try:
            with open(readme_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (IOError, OSError):
            return None

        for marker in _BOILERPLATE_MARKERS:
            if marker in content:
                return ctx.block(
                    f"⚠️ BOILERPLATE README: README.md contains scaffold marker "
                    f"'{marker}'. Use generate_project_readme() to replace with "
                    f"project-specific documentation."
                )

        return None
    except Exception as e:
        logger.debug(f"[PROJECT README] Advisory skipped: {e}")
        return None


# ── 6. BDD Literal Consistency (RCA-461 R-2) ────────────────────────────
# Wires check_bdd_literal_consistency() from bdd_literal_checker.py into the
# gate system. Previously dead code with zero gate callers — prices, URLs,
# emails, names, and brand names are cross-referenced manifest↔BDD.

@register_check(2.06, "BDD literal consistency", critical=True, web_only=True, gate="bdd")
def _blocking_bdd_literal_consistency(ctx):
    """Cross-reference ALL literals (prices, URLs, emails, names) in BDD vs manifest."""
    try:
        from python.helpers.bdd_literal_checker import check_bdd_literal_consistency
        from python.helpers.manifest_parser import _find_manifest_path

        if not ctx.project_dir:
            return None

        manifest_path = _find_manifest_path(ctx.project_dir)
        if not manifest_path:
            return None

        # Find BDD text file path (reuse existing helper logic)
        bdd_path = None
        for candidate in BDD_FILE_CANDIDATES:
            full_path = os.path.join(ctx.project_dir, candidate)
            if os.path.isfile(full_path):
                bdd_path = full_path
                break
        if not bdd_path:
            return None

        result = check_bdd_literal_consistency(manifest_path, bdd_path)
        if not result.get("consistent", True):
            mismatches = result.get("mismatches", [])
            # Only block on error-severity mismatches (not warnings like format diffs)
            errors = [m for m in mismatches if m.get("severity") == "error"]
            if errors:
                details = ", ".join(
                    f"{m['field']}: {m['bdd_value']}" for m in errors[:5]
                )
                return ctx.block(
                    f"⚠️ BDD LITERAL MISMATCH: {len(errors)} value(s) in BDD "
                    f"scenarios don't match content_manifest.json: {details}"
                )
        return None
    except Exception as e:
        logger.debug(f"[BDD LITERAL CONSISTENCY] Check skipped: {e}")
        return None


# ── 7. BDD Boilerplate Ratio ────────────────────────────────────────────

@register_check(2.08, "BDD boilerplate ratio", critical=True, web_only=True, gate="bdd")
def _blocking_bdd_boilerplate(ctx):
    """Check BDD scenarios aren't dominated by generic templates."""
    try:
        from python.helpers.bdd_generator_validation import check_bdd_boilerplate_ratio
        if not ctx.project_dir:
            return None
        bdd_text = _find_bdd_text(ctx.project_dir)
        if not bdd_text:
            return None
        result = check_bdd_boilerplate_ratio(bdd_text)
        if not result["quality_pass"]:
            return ctx.block(
                f"⚠️ BDD BOILERPLATE: {result['boilerplate_ratio']:.0%} of scenarios "
                f"are generic templates ({result['boilerplate_count']}/{result['total_scenarios']})"
            )
        return None
    except Exception as e:
        logger.debug(f"[BDD BOILERPLATE] Check skipped: {e}")
        return None
