"""
Quality gate checks — lib test coverage, blueprint requirement verification,
fetch-route completeness, content presence, stub endpoints, config coherence,
pre-build hints, tautological test detection, import-dependency consistency,
unused dependencies, integration strategy, schema-route consistency,
test file coverage, duplicate logic, TODO/hardcode detection,
SDK completeness, TDD placeholder detection, BDD requirement coverage,
route table reconciliation.

Deleted validators (common, manifest_packages, css_integrity, css_config_coherence,
prisma_provider_coherence, design_token_consumption, island_function_detector,
api_contract, type_coherence, client_component_ratio, manifest_completeness,
node_checks_framework) have been removed along with their consumer functions.
"""

import os
import re
import json
import logging
import difflib
from typing import Optional

from python.helpers.orchestrator_gate_integration_checks import (
    register_check,
    register_advisory,
    CheckContext,
)
from python.helpers.orchestrator_gate_common import format_gate_block

logger = logging.getLogger("agix.orchestrator_completion_gate")


@register_check(1.204, "Critical lib test coverage", gate="tdd")
def _check_lib_test_coverage(ctx: CheckContext):
    """Verify critical lib files in src/lib/ have corresponding test files.

    Business logic lives in src/lib/ — these files MUST have tests.
    We check for test files in: tests/lib/, __tests__/, or *.test.ts alongside.

    RCA Phase 2 (P2-5): scoring.ts shipped without tests in MainStreet.
    """
    if not ctx.project_dir:
        return None

    lib_dir = os.path.join(ctx.project_dir, "src", "lib")
    if not os.path.isdir(lib_dir):
        return None

    # Skip trivial/generated files
    skip_names = {"prisma.ts", "prisma.js", "utils.ts", "utils.js", "cn.ts"}

    lib_files = []
    for fname in os.listdir(lib_dir):
        if fname in skip_names:
            continue
        if fname.endswith((".ts", ".js")) and not fname.endswith((".d.ts", ".test.ts", ".spec.ts")):
            lib_files.append(fname)

    if not lib_files:
        return None

    # Search for test files
    untested = []
    for lib_file in lib_files:
        base = os.path.splitext(lib_file)[0]
        test_patterns = [
            os.path.join(ctx.project_dir, "tests", "lib", f"{base}.test.ts"),
            os.path.join(ctx.project_dir, "tests", "lib", f"{base}.test.js"),
            os.path.join(ctx.project_dir, "src", "lib", f"{base}.test.ts"),
            os.path.join(ctx.project_dir, "src", "lib", "__tests__", f"{base}.test.ts"),
            os.path.join(ctx.project_dir, "__tests__", "lib", f"{base}.test.ts"),
        ]
        if not any(os.path.isfile(p) for p in test_patterns):
            untested.append(lib_file)

    if untested:
        details = ", ".join(untested[:5])
        return ctx.block(
            f"⚠️ UNTESTED LIB FILES: {len(untested)} file(s) in src/lib/ have no "
            f"corresponding test file: {details}. "
            f"Create tests in tests/lib/ or alongside the file."
        )
    return None


@register_check(1.205, "Blueprint requirement verification", critical=True, gate="bdd")
def _check_blueprint_requirement_verification(ctx: CheckContext):
    """Verify architect_plan.json file specs actually exist on disk.

    Promoted from advisory to BLOCKING (RCA-306 Phase 3).
    Circuit breaker: 3 blocks max.
    """
    if not ctx.project_dir:
        return None

    BLUEPRINT_CB_KEY = "_blueprint_blocks"
    BLUEPRINT_CB_THRESHOLD = 3
    block_count = ctx.agent_data.get(BLUEPRINT_CB_KEY, 0)
    if block_count >= BLUEPRINT_CB_THRESHOLD:
        logger.warning(
            f"[BLUEPRINT GATE] Circuit breaker fired after {block_count} blocks. "
            f"Yielding to prevent death spiral."
        )
        return None

    from python.helpers.blueprint_req_bridge import generate_file_specs
    from python.helpers.post_execution_req_verifier import verify_file_spec

    file_specs = generate_file_specs(ctx.project_dir)
    if not file_specs:
        return None

    missing = []
    for spec in file_specs:
        result = verify_file_spec(ctx.project_dir, spec)
        if not result["verified"]:
            missing.append(result)

    if not missing:
        ctx.agent_data[BLUEPRINT_CB_KEY] = 0
        return None

    ctx.agent_data[BLUEPRINT_CB_KEY] = block_count + 1

    missing_summary = "\n".join(
        f"  - **{m['path']}**: {m['reason']}"
        for m in missing[:8]
    )
    overflow = f"\n  ... and {len(missing) - 8} more" if len(missing) > 8 else ""

    total = len(file_specs)
    verified = total - len(missing)

    return ctx.block(
        f"📋 BLUEPRINT VERIFICATION: {verified}/{total} file specs verified. "
        f"{len(missing)} file(s) from architect_plan.json are missing or incomplete:\n"
        f"{missing_summary}{overflow}\n\n"
        f"These files were specified in the architect's blueprint but don't exist "
        f"or don't meet their acceptance criteria. Delegate targeted fixes for "
        f"each missing file. (Block {block_count + 1}/{BLUEPRINT_CB_THRESHOLD})"
    )


@register_check(1.208, "Stub endpoint detection", critical=True, requires=["npm install"], web_only=True, gate="tdd")
def _check_stub_endpoints(ctx: CheckContext):
    """Detect placeholder/TODO API route handlers that would ship broken endpoints."""
    try:
        from python.helpers.post_execution_req_verifier import scan_for_stub_endpoints
        findings = scan_for_stub_endpoints(ctx.project_dir)
        if not findings:
            return None
        details = "\n".join(
            f"  - `{f['route']}`: {f['reason']}"
            for f in findings[:6]
        )
        return ctx.block(
            f"⛔ STUB ENDPOINTS: {len(findings)} API route(s) contain placeholder/empty content:\n"
            f"{details}\n\n"
            f"These endpoints will return empty or broken responses. Replace TODO/placeholder "
            f"content with real business logic, database queries, or proper error responses."
        )
    except Exception as e:
        logger.debug(f"[STUB DETECTOR] Check failed: {e}")
        return None


@register_advisory(1.2098, "Config coherence", web_only=True)
def _check_config_coherence(ctx: CheckContext):
    """Validate package.json dependencies have corresponding .env variables."""
    try:
        from python.helpers.config_coherence import validate_config_coherence
        result = validate_config_coherence(ctx.project_dir)
        if result is None or not result.get("missing_env_vars"):
            return None
        missing = result["missing_env_vars"]
        details = "\n".join(f"  - {v}" for v in missing[:6])
        return (
            f"⚠️ CONFIG COHERENCE: {len(missing)} expected env var(s) not found:\n"
            f"{details}\n\n"
            f"These packages are installed but their expected API keys/env vars "
            f"are missing from .env files. Add them to .env and .env.example."
        )
    except Exception as e:
        logger.debug(f"[CONFIG COHERENCE] Check failed: {e}")
        return None


@register_check(1.2095, "Prisma env coherence", web_only=True, gate="done")
def _check_prisma_env_coherence(ctx: CheckContext):
    """BLOCKING: Validate Prisma schema provider matches DATABASE_URL protocol."""
    try:
        from python.helpers.config_coherence import validate_prisma_env_coherence
        issues = validate_prisma_env_coherence(ctx.project_dir)
        if not issues:
            try:
                from python.helpers.check_sm_wiring import transition_check_sm
                transition_check_sm(ctx.agent_data, "prisma_env_1.2095", True)
            except Exception:
                pass
            return None
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "prisma_env_1.2095", False)
        except Exception:
            pass
        details = "\n".join(f"  - {i.get('message', i)}" for i in issues[:5])
        return (
            f"🛑 PRISMA ENV COHERENCE: {len(issues)} mismatch(es) detected:\n"
            f"{details}\n\n"
            f"Fix the DATABASE_URL provider to match your Prisma schema datasource."
        )
    except Exception as e:
        logger.debug(f"[PRISMA COHERENCE] Check failed: {e}")
        try:
            from python.helpers.check_sm_wiring import transition_check_sm
            transition_check_sm(ctx.agent_data, "prisma_env_1.2095", True)
        except Exception:
            pass
        return None


@register_advisory(1.92, "Pre-build type check", web_only=True)
def _check_pre_build_hint(ctx: CheckContext):
    """Inject one-shot `npx tsc --noEmit` hint before first build attempt."""
    try:
        from python.helpers.pre_build_advisor import get_pre_build_hint
        hint = get_pre_build_hint(ctx.agent_data)
        if hint:
            return hint
    except Exception as e:
        logger.debug(f"[PRE-BUILD] Check failed: {e}")
    return None


# ─── WB-6: Tautological Test Detection ──────────────────────────────────

@register_check(1.2066, "Test assertion quality", web_only=True, gate="tdd")
def _check_test_assertion_quality(ctx: CheckContext):
    """BLOCKING: reject when test files contain tautological assertions."""
    if not ctx.project_dir:
        return None

    TAUTOLOGY_CB_KEY = "_tautological_blocks"
    TAUTOLOGY_CB_THRESHOLD = 3
    block_count = ctx.agent_data.get(TAUTOLOGY_CB_KEY, 0)
    if block_count >= TAUTOLOGY_CB_THRESHOLD:
        logger.warning(
            f"[TAUTOLOGY GATE] Circuit breaker fired after {block_count} blocks. "
            f"Yielding to prevent death spiral."
        )
        return None

    try:
        from python.helpers.validators.tautological_test_detector import detect_tautological_tests
        result = detect_tautological_tests(ctx.project_dir)
        if result is None or result["tautological_count"] == 0:
            ctx.agent_data[TAUTOLOGY_CB_KEY] = 0
            return None
        findings = result.get("findings", [])[:5]
        details = "\n".join(
            f"  - {f['file']}:{f['line']}: {f['pattern']}"
            for f in findings
        )
        overflow = (
            f"\n  ... and {result['tautological_count'] - 5} more"
            if result["tautological_count"] > 5 else ""
        )

        ctx.agent_data[TAUTOLOGY_CB_KEY] = block_count + 1

        return ctx.block(
            f"⚠️ TAUTOLOGICAL TESTS: {result['tautological_count']} assertion(s) "
            f"test nothing (expect(literal).toBe(same_literal)):\n{details}{overflow}\n\n"
            f"Replace with behavioral assertions that call functions, render "
            f"components, or query APIs. "
            f"(Block {block_count + 1}/{TAUTOLOGY_CB_THRESHOLD})"
        )
    except Exception as e:
        logger.debug(f"[TAUTOLOGY] Check failed: {e}")
    return None


# [REMOVED] RCA-334 SS-3: Import-to-Dependency Validator — deleted with framework_structure_validator.py
# [REMOVED] ISS-02: Reverse Dependency Check — deleted with framework_structure_validator.py
# [REMOVED] U-6: Integration Strategy Conflict Detection — deleted with framework_structure_validator.py




# ─── RCA-354 Fix 1 (L3): Test File Coverage Gate ────────────────────────

# Files/dirs excluded from test-coverage requirements (trivial/generated).
_TRIVIAL_SKIP_PATTERNS = {
    "types", "interfaces", "__tests__", "tests", "__mocks__",
    "node_modules", ".next", "dist", "build",
}

_TRIVIAL_FILENAMES = {
    "index.ts", "index.tsx", "index.js", "index.jsx",
    "layout.tsx", "layout.jsx", "layout.ts", "layout.js",
    "loading.tsx", "loading.jsx", "loading.ts", "loading.js",
    "error.tsx", "error.jsx", "error.ts", "error.js",
    "not-found.tsx", "not-found.jsx",
    "page.tsx", "page.jsx", "page.ts", "page.js",
    "middleware.ts", "middleware.js",
    "tailwind.config.ts", "tailwind.config.js",
    "postcss.config.js", "postcss.config.mjs",
    "next.config.ts", "next.config.js", "next.config.mjs",
    "globals.css", "global.css",
    "utils.ts", "utils.js", "cn.ts",
    "prisma.ts", "prisma.js",
    "constants.ts", "constants.js",
}

_TRIVIAL_EXTENSIONS = {".css", ".scss", ".less", ".svg", ".png", ".jpg", ".ico", ".d.ts"}


def _is_trivial_source(relpath: str, filename: str) -> bool:
    """Determine if a source file is trivial (shouldn't require a test)."""
    if any(x in filename for x in [".test.", ".spec.", "__test__"]):
        return True
    if filename in _TRIVIAL_FILENAMES:
        return True
    _, ext = os.path.splitext(filename)
    if ext in _TRIVIAL_EXTENSIONS:
        return True
    parts = relpath.replace("\\", "/").split("/")
    for part in parts:
        if part in _TRIVIAL_SKIP_PATTERNS:
            return True
    return False


def _find_test_file(base: str, project_dir: str, source_relpath: str) -> bool:
    """Check if a test file exists for the given source file base name."""
    test_names = [
        f"{base}.test.ts", f"{base}.test.tsx",
        f"{base}.test.js", f"{base}.test.jsx",
        f"{base}.spec.ts", f"{base}.spec.tsx",
        f"{base}.spec.js", f"{base}.spec.jsx",
    ]
    source_dir = os.path.dirname(source_relpath)
    search_dirs = [
        os.path.join(project_dir, "__tests__"),
        os.path.join(project_dir, "tests"),
        os.path.join(project_dir, "__tests__", "lib"),
        os.path.join(project_dir, "tests", "lib"),
        os.path.join(project_dir, source_dir) if source_dir else "",
        os.path.join(project_dir, source_dir, "__tests__") if source_dir else "",
    ]
    for search_dir in search_dirs:
        if not search_dir or not os.path.isdir(search_dir):
            continue
        for test_name in test_names:
            if os.path.isfile(os.path.join(search_dir, test_name)):
                return True
    return False


def _scan_tdd_file_coverage(
    project_dir: str,
    bdd_specs: Optional[list] = None,
) -> Optional[dict]:
    """Scan project src/ for non-trivial source files and verify test coverage."""
    src_dir = os.path.join(project_dir, "src")
    if not os.path.isdir(src_dir):
        return None

    source_files = []
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in _TRIVIAL_SKIP_PATTERNS]
        for fname in files:
            if not fname.endswith((".ts", ".tsx", ".js", ".jsx")):
                continue
            relpath = os.path.relpath(os.path.join(root, fname), project_dir)
            if _is_trivial_source(relpath, fname):
                continue
            source_files.append(relpath)

    if not source_files:
        return None

    uncovered = []
    for relpath in source_files:
        fname = os.path.basename(relpath)
        base = os.path.splitext(fname)[0]
        if fname in ("route.ts", "route.js", "route.tsx", "route.jsx"):
            parent_dir = os.path.basename(os.path.dirname(relpath))
            if parent_dir and parent_dir != "api":
                base = parent_dir
        if not _find_test_file(base, project_dir, relpath):
            uncovered.append(relpath)

    coverage_ratio = 1.0 - (len(uncovered) / len(source_files)) if source_files else 1.0

    missing_bdd = []
    if bdd_specs and isinstance(bdd_specs, list):
        for spec in bdd_specs:
            test_file = spec.get("test_file", "")
            if test_file:
                full_path = os.path.join(project_dir, test_file)
                if not os.path.isfile(full_path):
                    missing_bdd.append(test_file)

    # RCA-470: Report when ANY source file lacks tests (was 0.5)
    should_report = coverage_ratio < 1.0 or len(missing_bdd) > 0

    if not should_report:
        return {
            "uncovered_count": 0,
            "uncovered_files": [],
            "coverage_ratio": coverage_ratio,
            "total_source_files": len(source_files),
            "missing_bdd_test_files": [],
        }

    return {
        "uncovered_count": len(uncovered),
        "uncovered_files": uncovered,
        "coverage_ratio": coverage_ratio,
        "total_source_files": len(source_files),
        "missing_bdd_test_files": missing_bdd,
    }


@register_check(1.122, "Test file coverage", critical=True, web_only=True, gate="tdd")
def _check_tdd_file_coverage(ctx: CheckContext):
    """Block when any non-trivial source file lacks a corresponding test file (RCA-470: 100%)."""
    if not ctx.project_dir:
        return None

    bdd_specs = ctx.agent_data.get("_test_specs", [])
    result = _scan_tdd_file_coverage(ctx.project_dir, bdd_specs=bdd_specs)
    if result is None:
        return None

    uncovered = result["uncovered_files"]
    missing_bdd = result.get("missing_bdd_test_files", [])

    if result["uncovered_count"] == 0 and not missing_bdd:
        return None

    parts = []
    if uncovered:
        file_list = ", ".join(f"`{f}`" for f in uncovered[:8])
        overflow = f" ... and {len(uncovered) - 8} more" if len(uncovered) > 8 else ""
        parts.append(
            f"TEST FILE COVERAGE: {result['uncovered_count']}/{result['total_source_files']} "
            f"source files lack tests ({result['coverage_ratio']:.0%} covered):\n"
            f"  {file_list}{overflow}"
        )
    if missing_bdd:
        bdd_list = ", ".join(f"`{f}`" for f in missing_bdd[:5])
        parts.append(
            f"BDD SPEC TEST FILES MISSING: {bdd_list}. "
            f"These test files were specified in bdd_specs but don't exist."
        )

    message = "\n\n".join(parts) + (
        "\n\nCreate test files for each uncovered source file. "
        "Every business logic file MUST have a corresponding test file."
    )
    return ctx.block(
        message,
        action=(
            f"Create test files for {len(uncovered)} uncovered source file(s). "
            f"Write tests in __tests__/ or colocated *.test.ts files."
        ),
    )


# ─── RCA-358 F-23: Duplicate Logic Detection ────────────────────────────

_EXPORT_FUNC_RE = re.compile(
    r'export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)',
)
_EXPORT_CONST_RE = re.compile(
    r'export\s+const\s+(\w+)\s*=',
)
_EXPORT_CLASS_RE = re.compile(
    r'export\s+(?:default\s+)?class\s+(\w+)',
)

_DUPLICATE_SIMILARITY_THRESHOLD = 0.70


def _extract_export_names(filepath: str) -> list[str]:
    """Extract exported function/const/class names from a TS/JS file."""
    try:
        with open(filepath, "r", errors="replace") as f:
            content = f.read()
    except (OSError, IOError):
        return []

    names = []
    names.extend(_EXPORT_FUNC_RE.findall(content))
    names.extend(_EXPORT_CONST_RE.findall(content))
    names.extend(_EXPORT_CLASS_RE.findall(content))
    return names


def _collect_exports_from_dirs(project_dir: str) -> list[tuple[str, str]]:
    """Collect (export_name, relative_filepath) from src/lib/ and src/utils/."""
    results = []
    for subdir in ("lib", "utils"):
        scan_dir = os.path.join(project_dir, "src", subdir)
        if not os.path.isdir(scan_dir):
            continue
        for root, _dirs, files in os.walk(scan_dir):
            for fname in files:
                if not fname.endswith((".ts", ".tsx", ".js", ".jsx")):
                    continue
                if any(x in fname for x in [".test.", ".spec.", "__test__"]):
                    continue
                filepath = os.path.join(root, fname)
                relpath = os.path.relpath(filepath, project_dir)
                for name in _extract_export_names(filepath):
                    results.append((name, relpath))
    return results


def _find_similar_pairs(
    exports: list[tuple[str, str]],
    threshold: float = _DUPLICATE_SIMILARITY_THRESHOLD,
) -> list[dict]:
    """Find pairs of exports with name similarity above the threshold."""
    similar = []
    seen = set()
    for i, (name_a, file_a) in enumerate(exports):
        for j, (name_b, file_b) in enumerate(exports):
            if j <= i:
                continue
            if name_a == name_b:
                continue
            pair_key = tuple(sorted([name_a, name_b]))
            if pair_key in seen:
                continue
            ratio = difflib.SequenceMatcher(None, name_a, name_b).ratio()
            if ratio >= threshold:
                seen.add(pair_key)
                similar.append({
                    "name_a": name_a,
                    "file_a": file_a,
                    "name_b": name_b,
                    "file_b": file_b,
                    "ratio": ratio,
                })
    return similar


@register_advisory(1.2039, "Duplicate logic", web_only=True)
def _check_duplicate_logic(ctx: CheckContext):
    """Detect potential duplicate logic by scanning for similar export names."""
    if not ctx.project_dir:
        return None

    exports = _collect_exports_from_dirs(ctx.project_dir)
    if len(exports) < 2:
        return None

    similar = _find_similar_pairs(exports)
    if not similar:
        return None

    capped = similar[:5]
    details = "\n".join(
        f"  - `{s['name_a']}` ({s['file_a']}) ↔ `{s['name_b']}` "
        f"({s['file_b']}) — {s['ratio']:.0%} similar"
        for s in capped
    )
    overflow = (
        f"\n  ... and {len(similar) - len(capped)} more"
        if len(similar) > len(capped) else ""
    )
    return (
        f"⚠️ POTENTIAL DUPLICATE LOGIC: {len(similar)} pair(s) of exported "
        f"functions/constants with >70% name similarity found in src/lib/ "
        f"and src/utils/:\n{details}{overflow}\n\n"
        f"Consider consolidating near-duplicate functions into a single "
        f"reusable implementation to reduce maintenance burden."
    )


# [REMOVED] F-6: BDD-to-Code TODO/Hardcode Detection — deleted with stub_detection.py



# ─── F-2: SDK Completeness (Reverse Wiring Check) ───────────────────────

@register_check(1.2092, "SDK completeness", critical=False, web_only=True, gate="done")
def _check_sdk_completeness(ctx: CheckContext):
    """Check: flag when .env vars exist but corresponding SDKs are missing."""
    if not ctx.project_dir:
        return None

    try:
        from python.helpers.config_coherence import verify_sdk_completeness
        findings = verify_sdk_completeness(ctx.project_dir)
        if not findings:
            return None

        details = "\n".join(f"  - {f['detail']}" for f in findings[:6])
        overflow = (
            f"\n  ... and {len(findings) - 6} more"
            if len(findings) > 6 else ""
        )
        return ctx.block(
            f"⚠️ SDK COMPLETENESS: {len(findings)} gap(s) in env → package → import chain:\n"
            f"{details}{overflow}\n\n"
            f"For each env var, ensure the corresponding SDK package is installed "
            f"in package.json AND imported in source code."
        )
    except Exception as e:
        logger.debug(f"[SDK COMPLETENESS] Check failed: {e}")
        return None


# ─── ITR-21 F-12: TDD Placeholder Detection ─────────────────────────────

_PLACEHOLDER_PATTERNS = [
    re.compile(r'throw\s+new\s+Error\s*\(\s*["\']TODO["\']', re.IGNORECASE),
    re.compile(r'//\s*(?:Placeholder|TODO|stub|not implemented)', re.IGNORECASE),
    re.compile(r'#\s*(?:Placeholder|TODO|stub|not implemented)', re.IGNORECASE),
    re.compile(r'^\s+pass\s*(?:#.*)?$', re.MULTILINE),  # Python empty test body (bare pass only)
]

_TEST_BODY_PATTERN = re.compile(
    r'(?:it|test|describe)\s*\(\s*["\'].*?["\']\s*,\s*(?:async\s*)?\(\s*\)\s*=>\s*\{(.*?)\}',
    re.DOTALL,
)


@register_check(1.2093, "TDD placeholder detection", critical=True, web_only=True, gate="tdd")
def _check_tdd_placeholder_files(ctx: CheckContext):
    """Detect test files where ALL test bodies are placeholders."""
    if not ctx.project_dir:
        return None

    test_dirs = [
        os.path.join(ctx.project_dir, "__tests__"),
        os.path.join(ctx.project_dir, "tests"),
        os.path.join(ctx.project_dir, "src", "__tests__"),
    ]

    all_placeholder_files = []

    for test_dir in test_dirs:
        if not os.path.isdir(test_dir):
            continue
        for root, _dirs, files in os.walk(test_dir):
            for fname in files:
                if not any(x in fname for x in [".test.", ".spec."]):
                    continue
                filepath = os.path.join(root, fname)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except (OSError, IOError):
                    continue

                bodies = _TEST_BODY_PATTERN.findall(content)
                if not bodies:
                    continue

                all_placeholder = True
                for body in bodies:
                    body_stripped = body.strip()
                    if not body_stripped:
                        continue
                    is_placeholder = any(
                        p.search(body_stripped) for p in _PLACEHOLDER_PATTERNS
                    )
                    if not is_placeholder and len(body_stripped) > 5:
                        all_placeholder = False
                        break

                if all_placeholder and len(bodies) > 0:
                    relpath = os.path.relpath(filepath, ctx.project_dir)
                    all_placeholder_files.append(relpath)

    if not all_placeholder_files:
        return None

    details = "\n".join(f"  - `{f}`" for f in all_placeholder_files[:8])
    overflow = (
        f"\n  ... and {len(all_placeholder_files) - 8} more"
        if len(all_placeholder_files) > 8 else ""
    )
    return ctx.block(
        f"⚠️ TDD PLACEHOLDER FILES: {len(all_placeholder_files)} test file(s) contain "
        f"ONLY placeholder bodies (throw new Error('TODO'), empty, or // Placeholder):\n"
        f"{details}{overflow}\n\n"
        f"These stubs pass `npm test` but verify NOTHING. Replace ALL placeholder "
        f"bodies with REAL assertions that test the requirement. A test file where "
        f"every body is `throw new Error('TODO')` is NOT a test — it is an "
        f"incomplete stub.\n\n"
        f"1. Read BDD THEN clauses for each REQ-ID\n"
        f"2. Replace placeholder bodies with real assertions\n"
        f"3. Run tests — verify they FAIL (Red phase)\n"
        f"4. Write production code to make tests pass"
    )


# ─── Upstream Testability: BDD Requirement Coverage ─────────────────────

@register_advisory(1.00151, "BDD requirement coverage (advisory)")
def _check_bdd_requirement_coverage(ctx: CheckContext):
    """Advisory: flag when requirements lack BDD scenarios."""
    if not ctx.project_dir:
        return None

    try:
        from python.helpers.validators.bdd_requirement_coverage import (
            validate_bdd_requirement_coverage,
        )
        from python.helpers.requirements_ledger import _ensure_ledger
        ledger = _ensure_ledger(ctx.agent_data)
        if not ledger or not ledger.get("requirements"):
            return None

        result = validate_bdd_requirement_coverage(ctx.project_dir, ledger)
        if result["uncovered_count"] == 0:
            return None

        uncovered = result["uncovered_req_ids"]
        details = ", ".join(uncovered[:8])
        overflow = f" ... +{len(uncovered) - 8} more" if len(uncovered) > 8 else ""

        return (
            f"⚠️ BDD COVERAGE GAP: {result['uncovered_count']}/{result['total_requirements']} "
            f"requirement(s) have no BDD scenario in docs/bdd-scenarios.md: "
            f"{details}{overflow}\n\n"
            f"Coverage ratio: {result['coverage_ratio']:.0%}. Requirements without "
            f"BDD scenarios give the code agent no testable acceptance criteria. "
            f"Run skeleton_generator or manually add GIVEN/WHEN/THEN scenarios."
        )
    except Exception as e:
        logger.debug(f"[BDD COVERAGE] Check failed: {e}")
        return None


# ─── F-10 (ITR-22): Route Table Reconciliation ─────────────────────────

_API_ROUTE_TABLE_RE = re.compile(
    r'\|\s*(?:GET|POST|PUT|PATCH|DELETE)\s*\|\s*(/api/[^|\s]+)',
    re.IGNORECASE,
)

_API_ROUTE_LIST_RE = re.compile(
    r'(?:^|\n)\s*[-*]\s*(?:GET|POST|PUT|PATCH|DELETE)?\s*[`]?(/api/[^`\s,)]+)',
    re.IGNORECASE,
)


def check_route_table_reconciliation(project_dir: str):
    """Check that API routes from architecture docs exist as route files."""
    if not os.path.isdir(project_dir):
        return None

    arch_paths = [
        os.path.join(project_dir, "docs", "architecture-spec.md"),
        os.path.join(project_dir, "docs", "architecture-design-phase-2.md"),
        os.path.join(project_dir, "docs", "architecture.md"),
    ]
    arch_content = None
    for arch_path in arch_paths:
        if os.path.isfile(arch_path):
            try:
                with open(arch_path, "r", errors="ignore") as f:
                    arch_content = f.read()
                break
            except IOError:
                continue

    if not arch_content:
        return None

    defined_routes = set()
    for match in _API_ROUTE_TABLE_RE.finditer(arch_content):
        route = match.group(1).strip().rstrip("/")
        defined_routes.add(route)
    for match in _API_ROUTE_LIST_RE.finditer(arch_content):
        route = match.group(1).strip().rstrip("/")
        defined_routes.add(route)

    if not defined_routes:
        return None

    existing = []
    missing = []
    for route in sorted(defined_routes):
        route_rel = route.lstrip("/")
        found = False
        for route_file in ["route.ts", "route.js", "route.tsx", "route.jsx"]:
            for prefix in ["src/app", "app"]:
                candidate = os.path.join(project_dir, prefix, route_rel, route_file)
                if os.path.isfile(candidate):
                    found = True
                    break
            if found:
                break
        if found:
            existing.append({"path": route})
        else:
            missing.append({"path": route})

    return {
        "defined_routes": len(defined_routes),
        "existing_routes": existing,
        "missing_routes": missing,
    }


@register_check(1.2075, "Route table reconciliation", critical=True, web_only=True, gate="done")
def _check_route_table_reconciliation(ctx: CheckContext):
    """Verify API routes in architecture.md exist as actual route.ts files."""
    result = check_route_table_reconciliation(ctx.project_dir)
    if result is None:
        return None
    missing = result.get("missing_routes", [])
    if not missing:
        return None

    routes_list = ", ".join(r["path"] for r in missing[:6])
    overflow = f" ... and {len(missing) - 6} more" if len(missing) > 6 else ""
    return ctx.block(
        f"⛔ MISSING ROUTE FILES: {len(missing)} API route(s) defined in architecture.md "
        f"but missing route.ts handler: {routes_list}{overflow}. "
        f"Create the missing route handler files."
    )


# [REMOVED] ADR-81: Widget Domain Coherence — deleted with stub_detection.py



# ─── U-10: API Route Coverage Advisory ────────────────────────────────

@register_advisory(1.2061, "API route coverage", web_only=True)
def _check_api_route_coverage(ctx: "CheckContext"):
    """Advisory: flag sitemap api_routes that have no matching route.ts file."""
    if not ctx.project_dir:
        return None

    sitemap_path = os.path.join(ctx.project_dir, "verification_sitemap.json")
    if not os.path.isfile(sitemap_path):
        return None

    try:
        with open(sitemap_path, "r", encoding="utf-8") as f:
            sitemap = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    api_routes = sitemap.get("api_routes", [])
    if not api_routes:
        return None

    missing = []
    for entry in api_routes:
        route = entry.get("path", "").rstrip("/")
        if not route:
            continue
        route_rel = route.lstrip("/")
        found = False
        for route_file in ["route.ts", "route.js", "route.tsx", "route.jsx"]:
            for prefix in ["src/app", "app"]:
                candidate = os.path.join(ctx.project_dir, prefix, route_rel, route_file)
                if os.path.isfile(candidate):
                    found = True
                    break
            if found:
                break
        if not found:
            missing.append(route)

    if not missing:
        return None

    routes_list = "\n".join(f"  - {r}" for r in missing[:8])
    overflow = f"\n  ... and {len(missing) - 8} more" if len(missing) > 8 else ""
    return (
        f"⚠️ MISSING API ROUTES: {len(missing)} route(s) in verification_sitemap.json "
        f"have no handler file (route.ts / route.js):\n{routes_list}{overflow}\n\n"
        f"Create the missing route handler files."
    )



# Backward-compat alias — css_integrity was deleted; alias to test assertion quality check
_check_css_integrity = _check_test_assertion_quality


@register_check(1.51, "Hardcoded secrets", critical=True, gate="done")
def _check_secrets(ctx: CheckContext):
    """Verify that no hardcoded secrets exist in the source files."""
    if not ctx.project_dir:
        return None

    from python.helpers.secret_guard import scan_file, should_scan_file

    found_secrets = []
    for root, dirs, files in os.walk(ctx.project_dir):
        # Skip node_modules and dot folders
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        for file in files:
            filepath = os.path.join(root, file)
            if should_scan_file(filepath):
                secrets = scan_file(filepath)
                if secrets:
                    rel_path = os.path.relpath(filepath, ctx.project_dir)
                    found_secrets.append((rel_path, secrets))

    if found_secrets:
        details = ", ".join(f"{path} ({s['description']})" for path, list_s in found_secrets for s in list_s[:2])
        return ctx.block(f"🔴 HARDCODED SECRETS: {details}. Move to .env immediately.")

    return None


@register_check(1.2091, "Design token CSS consumption", critical=True, web_only=True, gate="tdd")
def _check_design_token_css_consumption(ctx: CheckContext):
    """BLOCKING: flag when design-tokens.json colors are not consumed by CSS."""
    if not ctx.project_dir:
        return None

    try:
        # Self-contained implementation to avoid missing file dependencies
        token_path = os.path.join(ctx.project_dir, "design-tokens.json")
        if not os.path.isfile(token_path):
            return None
        with open(token_path, "r", encoding="utf-8") as f:
            tokens = json.load(f)

        colors = []
        def _extract_colors(val):
            if isinstance(val, str):
                if val.startswith("#"):
                    colors.append(val.lower())
            elif isinstance(val, dict):
                for v in val.values():
                    _extract_colors(v)
        _extract_colors(tokens.get("colors", {}))

        if not colors:
            return None

        files_to_check = []
        for rel in ["src/app/globals.css", "app/globals.css", "tailwind.config.ts", "tailwind.config.js"]:
            candidate = os.path.join(ctx.project_dir, rel)
            if os.path.isfile(candidate):
                files_to_check.append(candidate)

        consumed = False
        for filepath in files_to_check:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().lower()
            if any(c in content for c in colors):
                consumed = True
                break

        if consumed:
            return None

        return ctx.block(
            f"⛔ DESIGN TOKEN GAP: design-tokens.json has {len(colors)} "
            f"color value(s), but NONE appear in globals.css or tailwind.config.\n"
            f"Files checked: {', '.join(os.path.basename(f) for f in files_to_check) if files_to_check else 'none found'}\n\n"
            f"The frontend agent produced design tokens, but the code agent did "
            f"not consume them. Update globals.css :root vars and/or "
            f"tailwind.config.ts theme.extend.colors to use the token values."
        )
    except Exception as e:
        logger.debug(f"[DESIGN TOKEN] Check failed: {e}")
        return None

