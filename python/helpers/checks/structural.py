"""
Structural gate checks — build artifacts, version drift, build pollution.

These checks validate the foundational structure of a web project:
  - Build artifact existence
  - Build pass verification
  - Version drift detection
  - Build pollution detection
"""

import os
import logging

from python.helpers.orchestrator_gate_integration_checks import (
    register_check,
    register_advisory,
    CheckContext,
)
from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS


logger = logging.getLogger("agix.orchestrator_completion_gate")


# KILLED (Gate Audit Iteration 19): Visual assets (order 1.115)
# Reason: Agents cannot satisfy visual asset requirements via re-delegation
# and the check caused death spirals. Removed entirely from all registries.

# KILLED (Gate Audit Iteration 19): Scaffold image cleanup (order 1.116)
# Reason: Agents cannot clean scaffold images via re-delegation.
# Removed entirely from all registries.

# RCA-ITR5 ISSUE-7: Version drift advisory (revived from KILLED state).

@register_advisory(1.101, "Version drift", web_only=True)
def _check_version_drift(ctx: CheckContext):
    """Detect version drift between versions.lock.json and package.json."""
    try:
        from python.helpers.validators.version_pinning import check_version_drift
    except ImportError:
        return None

    result = check_version_drift(ctx.project_dir)
    if result is None:
        return None
    if result.get("has_drift"):
        drifted = result["drifted_packages"][:5]
        drift_lines = ", ".join(
            f"`{d['name']}` ({d['expected']} → {d['actual']})"
            for d in drifted
        )
        return ctx.block(
            f"⚠️ VERSION DRIFT: {len(result['drifted_packages'])} package(s) differ "
            f"from researched versions: {drift_lines}. Consider pinning to "
            f"researched versions in versions.lock.json."
        )
    return None


@register_check(1.107, "Build artifact exists", critical=True, web_only=True, gate="done")
def _check_build_artifact_exists(ctx: CheckContext):
    """F-6 (ITR-28): Verify production build output exists before delivery."""
    if not ctx.project_dir:
        return None
    try:
        from python.helpers.build_verification import check_build_exists
        result = check_build_exists(ctx.project_dir)
        if not result["built"]:
            framework = result.get("framework", "unknown")
            return ctx.block(
                f"⛔ NO BUILD ARTIFACTS: Production build not found for "
                f"{framework} project. Expected build output in "
                f"{result.get('expected_path', '.next/ or dist/')}.",
                action=(
                    "Run `npm run build` in the project directory. "
                    "Verify the build completes without errors before delivery."
                ),
            )
        return None
    except Exception as e:
        logging.getLogger('agix.checks.structural').debug(
            f"[BUILD ARTIFACT] Check failed: {e}"
        )
        return None


@register_advisory(1.108, "Build passes", web_only=True)
def _check_build_passes_advisory(ctx):
    """Advisory: verify project build passes without errors.
    Wired from build_pass_check.py — addresses Class C (Build Loops, 7 audits).
    SS-3: Enriches build failure messages with cross-module error classification.
    """
    try:
        from python.helpers.validators.build_pass_check import check_build_passes
        result = check_build_passes(project_dir=ctx.project_dir)
        if result is None:
            return None
        if not result.get("passed", True):
            reason = result.get("reason", "Unknown build failure")
            build_output = result.get("output", "")

            # SS-3: Classify cross-module errors for actionable guidance
            classified = classify_cross_module_errors(build_output)
            if classified:
                guidance_lines = [f"  • {c['category']}: {c['guidance']}" for c in classified[:3]]
                guidance = "\n".join(guidance_lines)
                return (
                    f"⚠️ BUILD CHECK ADVISORY: {reason}\n"
                    f"Cross-module errors detected:\n{guidance}"
                )

            return f"⚠️ BUILD CHECK ADVISORY: {reason}"
        return None
    except Exception as e:
        logger.debug(f"[BUILD PASSES] Validator error: {e}")
        return None


@register_advisory(1.110, "Build pollution")
def _check_build_pollution_advisory(ctx):
    """Advisory: detect leftover tmp/ directory that can corrupt builds."""
    if not ctx.project_dir:
        return None

    tmp_dir = os.path.join(ctx.project_dir, "tmp")
    if not os.path.isdir(tmp_dir):
        return None

    try:
        contents = os.listdir(tmp_dir)
    except OSError:
        return None

    if not contents:
        return None

    content_list = ", ".join(contents[:5])
    return (
        f"⚠️ BUILD POLLUTION ADVISORY: tmp/ directory contains "
        f"leftover content ({content_list}). This can corrupt webpack/Next.js "
        f"builds. Remove tmp/ directory or ensure git_publish cleanup completed."
    )


# ─── Boilerplate Content Check (RCA-234) ─────────────────────────────────────

def _check_boilerplate_content(project_dir, findings=None):
    """Check for boilerplate/default metadata in Next.js project files.

    RCA-234 FIX: Targeted messaging for two distinct boilerplate issues:
    1. Metadata export boilerplate (layout.tsx/page.tsx with default metadata)
    2. Body content boilerplate (generic placeholder content)

    Returns targeted feedback distinguishing metadata vs body content issues
    so the agent knows exactly what to fix — the Metadata export in layout.tsx
    or the page body content in the application pages.

    Args:
        project_dir: Absolute path to the project directory.
        findings: Optional pre-computed findings dict. If None, scans project.

    Returns:
        Warning string if boilerplate found, None if clean.
    """
    import os
    if findings is None:
        findings = {}

    metadata_findings = findings.get("metadata_findings", [])
    body_findings = findings.get("body_findings", [])

    if not metadata_findings and not body_findings:
        # Scan for default metadata in layout.tsx
        layout_path = os.path.join(project_dir, "src", "app", "layout.tsx")
        if not os.path.isfile(layout_path):
            layout_path = os.path.join(project_dir, "app", "layout.tsx")

        if os.path.isfile(layout_path):
            try:
                with open(layout_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # Check for default metadata export
                if "My App" in content or "Create Next App" in content:
                    metadata_findings = ["layout.tsx contains default Metadata export"]
            except OSError:
                pass

    if not metadata_findings and not body_findings:
        return None

    parts = []
    if metadata_findings:
        parts.append(
            "⚠️ DEFAULT METADATA DETECTED in layout.tsx — "
            "Update the `export const metadata: Metadata` object with real "
            "app title, description, and SEO metadata. "
            "Find: `export const metadata` in `src/app/layout.tsx` and replace "
            "with project-specific values."
        )
    if body_findings:
        parts.append(
            "⚠️ BOILERPLATE BODY CONTENT DETECTED — "
            "Replace generic placeholder text with real application content."
        )

    return " | ".join(parts) if parts else None


def check_default_metadata(project_dir):
    """Check if the project has default/boilerplate metadata in layout.tsx.

    Returns structured findings dict with 'has_default' key, or None if
    the project directory doesn't exist or can't be scanned.

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        {"has_default": bool, "file": str, "indicators": list} or None.
    """
    import os
    if not os.path.isdir(project_dir):
        return None

    for layout_rel in ["src/app/layout.tsx", "app/layout.tsx"]:
        layout_path = os.path.join(project_dir, layout_rel)
        if not os.path.isfile(layout_path):
            continue
        try:
            with open(layout_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue

        indicators = []
        if "My App" in content:
            indicators.append("default title 'My App'")
        if "Create Next App" in content:
            indicators.append("scaffold title 'Create Next App'")
        if "A Next.js app" in content or "A Next.js project" in content:
            indicators.append("generic description")

        return {
            "has_default": bool(indicators),
            "file": layout_rel,
            "indicators": indicators,
        }

    return {"has_default": False, "file": None, "indicators": []}


# ─── Registered Boilerplate Gate Check (RCA-234 / RCA-306) ───────────────────

@register_check(0.6, "Boilerplate content", critical=True, web_only=True, gate="tdd")
def _check_boilerplate_gate(ctx):
    """Gate check: Detect and block on boilerplate/default content.

    RCA-234 FIX: Targets both Metadata export boilerplate in layout.tsx
    (default title/description) and generic body content placeholder text.
    Provides targeted feedback distinguishing the two issue types so the
    agent knows exactly what to fix.
    """
    result = _check_boilerplate_content(ctx.project_dir)
    if result:
        return ctx.block(result)
    return None


# ─── RCA-236 (RC-6.1): Build Cache Health Gate ──────────────────────────────

@register_check(1.93, "Build cache health", critical=True, web_only=True, gate="done")
def _check_build_cache(ctx: CheckContext):
    """Skip build cache check if dev server is running; otherwise verify .next/ state.

    RC-6.1: When `next dev` runs after `npm run build`, it can modify .next/
    state, causing build cache validation to false-positive. Skip the check
    when dev server is already started.
    """
    # RC-6.1: Dev server modifies .next/ — skip cache check to avoid false positives
    if ctx.agent_data.get("_dev_server_started"):
        return None

    if not ctx.project_dir:
        return None

    next_dir = os.path.join(ctx.project_dir, ".next")
    if not os.path.isdir(next_dir):
        return None

    # Check that build-manifest.json exists — if it's absent after build, cache is broken
    build_manifest = os.path.join(next_dir, "build-manifest.json")
    if not os.path.isfile(build_manifest):
        return ctx.block(
            "⛔ BUILD CACHE CORRUPT: .next/ directory exists but build-manifest.json "
            "is missing, indicating a corrupted or incomplete build cache. "
            "Run `npm run build` to regenerate the build artifacts."
        )

    return None


# ─── RCA-251 (§9): Manifest Fidelity Gate ────────────────────────────────────

MAX_MANIFEST_FIDELITY_BLOCKS = 3  # Circuit breaker: allow 3 blocks before lifting

@register_check(1.95, "Manifest fidelity", critical=True, web_only=True, gate="bdd")
def _check_manifest_fidelity(ctx: CheckContext):
    """Verify that values promised in content_manifest.json appear in SOURCE CODE.

    RCA-251 / RCA-253 Fix: The primary instruction to agents must be to fix
    SOURCE CODE (*.tsx, *.ts, *.jsx files), NOT to update manifest JSON.
    The manifest is a planning doc — the truth is in the source code.

    Circuit breaker: after MAX_MANIFEST_FIDELITY_BLOCKS blocks, lift the gate
    to prevent infinite deadlocks when the project structure is unusual.

    Stores violations in agent_data['_pending_fidelity_violations'] for
    supervisor propagation to subordinates.
    """
    if not ctx.project_dir:
        return None

    # Circuit breaker: count consecutive fidelity blocks
    block_count = ctx.agent_data.get("_manifest_fidelity_block_count", 0)
    if block_count >= MAX_MANIFEST_FIDELITY_BLOCKS:
        logger.debug(
            f"[MANIFEST FIDELITY] Circuit breaker: {block_count} blocks exceeded "
            f"MAX_MANIFEST_FIDELITY_BLOCKS={MAX_MANIFEST_FIDELITY_BLOCKS}, lifting gate."
        )
        ctx.agent_data["_manifest_fidelity_block_count"] = 0
        return None

    # Find content-manifest.json (RCA-457: check both hyphen + underscore)
    manifest_paths = [
        os.path.join(ctx.project_dir, "docs", "content-manifest.json"),
        os.path.join(ctx.project_dir, "content_manifest.json"),
        os.path.join(ctx.project_dir, "content-manifest.json"),
        os.path.join(ctx.project_dir, "docs", "content_manifest.json"),
    ]
    manifest_path = None
    for p in manifest_paths:
        if os.path.isfile(p):
            manifest_path = p
            break

    if not manifest_path:
        return None

    # RCA-244: Retrieve the user's original prompt for context
    # (used for fidelity comparison between original intent and manifest)
    try:
        from python.helpers.boomerang_context import get_original_user_message
        _original_user_prompt = get_original_user_message(ctx.agent)
    except Exception:
        _original_user_prompt = ""

    try:
        import json as _json
        with open(manifest_path, "r", encoding="utf-8", errors="replace") as f:
            manifest = _json.load(f)
    except (OSError, ValueError):
        return None

    # Collect "anchor" values — exact strings that must appear in source code
    anchors = []
    if isinstance(manifest, dict):
        for section_key in ("copy", "headings", "cta_text", "hero_text", "features"):
            section = manifest.get(section_key)
            if isinstance(section, list):
                for item in section:
                    if isinstance(item, str) and len(item) > 10:
                        anchors.append(item)
                    elif isinstance(item, dict):
                        for v in item.values():
                            if isinstance(v, str) and len(v) > 10:
                                anchors.append(v)
            elif isinstance(section, dict):
                for v in section.values():
                    if isinstance(v, str) and len(v) > 10:
                        anchors.append(v)

    if not anchors:
        return None

    # Scan source files for missing anchors
    src_dirs = [
        os.path.join(ctx.project_dir, "src"),
        os.path.join(ctx.project_dir, "app"),
        os.path.join(ctx.project_dir, "components"),
    ]

    source_content = []
    for src_dir in src_dirs:
        if not os.path.isdir(src_dir):
            continue
        for root, _dirs, files in os.walk(src_dir):
            for fname in files:
                if any(fname.endswith(ext) for ext in (".tsx", ".ts", ".jsx", ".js")):
                    try:
                        with open(os.path.join(root, fname), "r",
                                  encoding="utf-8", errors="replace") as f:
                            source_content.append(f.read())
                    except OSError:
                        continue

    if not source_content:
        return None

    combined = "\n".join(source_content)
    violations = [a for a in anchors[:20] if a not in combined]

    if not violations:
        ctx.agent_data.pop("_manifest_fidelity_block_count", None)
        ctx.agent_data["_pending_fidelity_violations"] = []
        return None

    # Store violations for supervisor propagation
    ctx.agent_data["_pending_fidelity_violations"] = [
        {"type": "missing", "expected_value": v, "detail": "Not found in source"}
        for v in violations
    ]
    ctx.agent_data["_manifest_fidelity_block_count"] = block_count + 1

    # RCA-461 R-4: Inject violations into test skeleton expected_literals.
    # The re-delegated agent will get FAILING TESTS for the exact missing
    # values, not just advisory brief text.
    inject_fidelity_violations_into_skeleton(ctx.project_dir, violations)

    details = "\n".join(f"  - \"{v[:80]}\"" for v in violations[:6])
    overflow = f"\n  ... and {len(violations) - 6} more" if len(violations) > 6 else ""

    return ctx.block(
        f"⛔ MANIFEST FIDELITY FAILURE: {len(violations)} value(s) from "
        f"content_manifest.json are MISSING from SOURCE CODE (.tsx, .ts, .jsx files):\n"
        f"{details}{overflow}\n\n"
        f"PRIMARY ACTION: Delegate the fix to a code agent using call_subordinate with "
        f"profile='code'. Instruct it to open the relevant .tsx/.ts component files and "
        f"ensure each promised value appears verbatim in the rendered JSX. "
        f"Do NOT just update content_manifest.json.\n\n"
        f"SECONDARY ACTION: After fixing source code, verify content_manifest.json "
        f"and requirements_ledger.json reflect the actual values in the code.",
        action=(
            "Delegate to code agent (call_subordinate, profile='code'): open each .tsx/.ts "
            "source component, find the section rendering this content, "
            "and replace placeholder/missing text with the exact values from the manifest. "
            "Source code is the truth — fix components first, update manifests second."
        ),
    )


# ─── Manifest Code Fidelity Gate (Wire 4) ────────────────────────────────────
# RCA-461 Bug #2: Demoted from @register_check to @register_advisory.
# Check 1.95 (_check_manifest_fidelity) is the canonical blocking gate with
# circuit breaker, TDD injection, and violation propagation. This check uses
# a DIFFERENT extraction algorithm (bdd_generator_validation), so keeping it
# as blocking caused confusing double-blocks with slightly different violation
# sets. Now advisory-only — provides additional detection without blocking.

@register_advisory(1.96, "Manifest code fidelity", web_only=True)
def _check_manifest_code_fidelity(ctx):
    """Advisory: verify manifest literals appear in source code.

    Uses bdd_generator_validation's extraction (different from check 1.95).
    Non-blocking — check 1.95 is the canonical blocking gate.
    """
    try:
        from python.helpers.bdd_generator_validation import check_manifest_code_fidelity
        if not ctx.project_dir:
            return None
        result = check_manifest_code_fidelity(ctx.project_dir)
        missing = result.get("missing", [])
        if missing:
            score = result.get("fidelity_score", 0)
            return (
                f"⚠️ CONTENT FIDELITY (advisory): {len(missing)} manifest values "
                f"missing from source code (score: {score:.0%}): {', '.join(missing[:5])}"
            )
        return None
    except Exception as e:
        logger.debug(f"[MANIFEST FIDELITY] Advisory check skipped: {e}")
        return None

# ─── RCA-461 R-4: Fidelity-Violation-Triggered TDD Regeneration ─────────────
# When fidelity violations are detected, inject missing values into the test
# skeleton's expected_literals. The re-delegated agent then gets FAILING TESTS
# for the exact missing values instead of advisory brief text.

def inject_fidelity_violations_into_skeleton(
    project_dir: str, violations: list
) -> bool:
    """Inject fidelity violations into test skeleton expected_literals.

    For each violation string, adds it to EVERY requirement's expected_literals
    in the test skeleton (since we don't know which requirement owns the value).
    De-duplicates to avoid adding the same literal twice.

    Args:
        project_dir: Path to the project directory.
        violations: List of string values missing from source code.

    Returns:
        True if the skeleton was updated, False otherwise.
    """
    if not violations or not project_dir:
        return False

    skeleton_path = os.path.join(project_dir, "docs", "test-skeleton.json")
    if not os.path.isfile(skeleton_path):
        return False

    try:
        import json as _json
        with open(skeleton_path, "r", encoding="utf-8") as f:
            skeleton = _json.load(f)

        updated = False
        for req in skeleton.get("requirements", []):
            existing = set(req.get("expected_literals", []))
            new_lits = [v for v in violations if v not in existing]
            if new_lits:
                req.setdefault("expected_literals", []).extend(new_lits)
                updated = True

        if updated:
            with open(skeleton_path, "w", encoding="utf-8") as f:
                _json.dump(skeleton, f, indent=2)
            logger.info(
                f"[R-4] Injected {len(violations)} fidelity violations into "
                f"test skeleton expected_literals"
            )
            # RCA-461 R-4: Invalidate TDD idempotency cache so the generator
            # re-runs and picks up the newly injected expected_literals.
            hash_path = os.path.join(project_dir, "docs", "tdd", ".tdd_hash")
            if os.path.isfile(hash_path):
                try:
                    os.remove(hash_path)
                    logger.info("[R-4] Deleted .tdd_hash to force TDD regeneration")
                except OSError:
                    pass

        return updated
    except (OSError, ValueError, KeyError) as e:
        logger.debug(f"[R-4] Failed to inject violations: {e}")
        return False


# Backward-compat aliases
_check_blueprint = _check_manifest_fidelity
_check_scaffold_only = _check_boilerplate_gate


# ── SS-4: Component Spec Compliance Gate ───────────────────────────────
#
# Verifies that components listed in component-spec.md actually exist
# as files in the project. Uses case-insensitive, kebab-case-aware
# file matching. Escape hatch after 3 blocks via gate_check.
# (MainStreet ITR-44 RCA, SS-4)


def _normalize_component_name(name: str) -> str:
    """Normalize a component name for fuzzy matching.

    'HeroSection' → 'herosection'
    'hero-section' → 'herosection'
    'Hero_Section' → 'herosection'
    """
    return name.lower().replace("-", "").replace("_", "").replace(" ", "")


def _extract_spec_component_names(spec_content: str) -> list:
    """Extract component names from component-spec.md.

    Looks for ## headings as component names.
    """
    names = []
    for line in spec_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("## #"):
            name = stripped[3:].strip()
            if name and len(name) > 2:
                names.append(name)
    return names


def _find_project_component_files(project_dir: str) -> set:
    """Walk project for component-like files, return normalized names."""
    component_exts = {".tsx", ".jsx", ".vue", ".svelte", ".ts", ".js", ".py"}
    found = set()
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in DEFAULT_PROJECT_SKIP_DIRS]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in component_exts:
                base = os.path.splitext(fname)[0]
                found.add(_normalize_component_name(base))
    return found


@register_check(1.17, "Component spec compliance", critical=False, web_only=True, gate="done")
def _check_component_spec_compliance(ctx):
    """SS-4: Verify component-spec.md components exist in the project.

    Layer 1 (fast): Parse component-spec.md for ## headings, then
                    walk project for matching files using case-insensitive,
                    kebab-case-aware normalization.
    Escape hatch: After 3 blocks (via gate_check), allow through.

    This check is advisory (critical=False) — it warns but doesn't
    death-spiral the agent.
    """
    if not ctx.project_dir or not os.path.isdir(ctx.project_dir):
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "component_spec_1.17", True)
        except Exception:
            pass
        return None

    # Read component spec — RCA-461 Bug #4: use canonical docs/ path
    spec_path = os.path.join(ctx.project_dir, "docs", "component-spec.md")
    if not os.path.isfile(spec_path):
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "component_spec_1.17", True)
        except Exception:
            pass
        return None

    try:
        with open(spec_path, "r", encoding="utf-8", errors="ignore") as f:
            spec_content = f.read()
    except (IOError, OSError):
        return None

    if not spec_content.strip():
        return None

    # Extract spec component names
    spec_names = _extract_spec_component_names(spec_content)
    if not spec_names:
        return None

    # Escape hatch check
    from python.helpers.universal_gate_budget import gate_check
    if gate_check(ctx.agent_data, "component_spec"):
        return None

    # Find project component files
    project_names = _find_project_component_files(ctx.project_dir)

    # Match: normalized spec name must appear in project file names
    missing = []
    for name in spec_names:
        normalized = _normalize_component_name(name)
        if normalized not in project_names:
            missing.append(name)

    if not missing:
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "component_spec_1.17", True)
        except Exception:
            pass
        return None

    missing_str = ", ".join(missing[:5])
    overflow = f" ... and {len(missing) - 5} more" if len(missing) > 5 else ""

    try:
        from python.helpers.check_sm_wiring import transition_check_sm
        transition_check_sm(ctx.agent_data, "component_spec_1.17", False)
    except Exception:
        pass
    return ctx.block(
        f"⚠️ COMPONENT SPEC: {len(missing)}/{len(spec_names)} components "
        f"from component-spec.md are missing: {missing_str}{overflow}. "
        f"Create the component files in src/components/ to match the spec.",
        action=(
            f"Create the missing component files: {missing_str}. "
            f"Follow the component-spec.md for structure and props."
        ),
    )

# ── SS-3: Universal Cross-Module Error Classification ──────────────────
#
# Parses build tool output (tsc, python, go, rust) and classifies
# cross-module reference errors with actionable guidance.
# Language-agnostic: works for any language by parsing the language's
# own build errors, not reinventing a type checker.
# (MainStreet ITR-44 RCA, SS-3)

import re as _re

_CROSS_MODULE_ERROR_PATTERNS = [
    # TypeScript / JavaScript
    (
        _re.compile(r"Property '(\w+)' does not exist on type"),
        "TS undefined property",
        "Create or export the property '{symbol}' on the type. Check the type definition.",
    ),
    (
        _re.compile(r"Module '(.+?)' has no exported member '(\w+)'"),
        "TS missing export",
        "Module {extra} does not export '{symbol}'. Add 'export' to the function/const declaration.",
    ),
    (
        _re.compile(r"Cannot find name '(\w+)'"),
        "TS undefined reference",
        "'{symbol}' is not defined. Import it from the correct module or create it.",
    ),
    # Python
    (
        _re.compile(r"ImportError: cannot import name '(\w+)'"),
        "Python import error",
        "'{symbol}' does not exist in the source module. Create the function/class/variable.",
    ),
    (
        _re.compile(r"ModuleNotFoundError: No module named '(\w+)'"),
        "Python missing module",
        "Module '{symbol}' does not exist. Create the file or install the package.",
    ),
    # Go
    (
        _re.compile(r"undefined: (\w+)"),
        "Go undefined reference",
        "'{symbol}' is not defined. Create the function or import the correct package.",
    ),
    # Rust
    (
        _re.compile(r"unresolved import `(\w+)`"),
        "Rust unresolved import",
        "Module '{symbol}' cannot be resolved. Add it as a dependency or create the module.",
    ),
]


def classify_cross_module_errors(stderr: str) -> list:
    """Classify cross-module reference errors from build output.

    Universal: works for TypeScript, Python, Go, and Rust by parsing
    each language's native error format.

    Args:
        stderr: Build stderr/stdout output to scan.

    Returns:
        List of dicts, each with:
          - category (str): Error category (e.g., "TS missing export")
          - symbol (str): The undefined/missing symbol name
          - guidance (str): Actionable fix instruction
          - match (str): The original error line that matched
          - extra (str, optional): Extra context (e.g., module path)
    """
    if not stderr or not stderr.strip():
        return []

    results = []
    seen = set()  # Deduplicate by (category, symbol)

    for line in stderr.splitlines():
        for pattern, category, guidance_template in _CROSS_MODULE_ERROR_PATTERNS:
            match = pattern.search(line)
            if match:
                groups = match.groups()
                # For patterns with 2 groups (e.g., Module + member),
                # symbol is the LAST group, extra is the first
                if len(groups) >= 2:
                    extra = groups[0]
                    symbol = groups[-1]
                else:
                    symbol = groups[0] if groups else "unknown"
                    extra = ""

                dedup_key = (category, symbol)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                guidance = guidance_template.format(symbol=symbol, extra=extra)

                results.append({
                    "category": category,
                    "symbol": symbol,
                    "guidance": guidance,
                    "match": line.strip(),
                    **({"extra": extra} if extra else {}),
                })
                break  # One classification per line

    return results
