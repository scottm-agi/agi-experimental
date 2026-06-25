"""TDD Generator Helpers — route/SDK extraction, detection, escaping.

Extracted from the original tdd_generator.py monolith.
Contains utility functions used by tdd_generator_creation.py.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from python.helpers.manifest_parser import parse_manifest
from python.helpers.planning_paths import get_path as _planning_path

logger = logging.getLogger("tdd_generator")

from python.helpers.tdd_generator_constants import _DEFERRED_CATEGORIES, _GARBAGE_LITERALS, _SDK_NAMES, _HIDDEN_ELEMENT_FILTER_JS

def _extract_route(text: str) -> str:
    """Extract route path from requirement text.

    ITR-33 FIX-A4: Extracts leading route pattern from req text.
    E.g., '/privacy — Privacy page' → '/privacy'
          '/r/[slug]/audit — Audit page' → '/r/[slug]/audit'
          'Resend email integration' → ''

    Args:
        text: Requirement text string.

    Returns:
        Route path string, or empty string if no route found.
    """
    match = re.match(r'^(/[a-zA-Z0-9_/\[\].-]+)', text.strip())
    return match.group(1).rstrip('.') if match else ""

def _extract_sdk_name(text: str) -> str:
    """Extract SDK package name from requirement text.

    ITR-33 FIX-A4: Looks up known SDK keywords in the text and
    returns the corresponding npm package name.

    Args:
        text: Requirement text string.

    Returns:
        SDK package name (e.g., 'resend'), or empty string.
    """
    text_lower = text.lower()
    for name, package in _SDK_NAMES.items():
        if name in text_lower:
            return package
    return ""


def _load_design_tokens(project_dir: str) -> dict:
    """Load design-tokens.json from project docs directory.

    FIX-6: Used by _generate_typescript_stubs and _generate_python_stubs
    to inject CSS custom property assertions into TDD stubs.

    Returns:
        Parsed dict from design-tokens.json, or {} if missing/invalid.
    """
    tokens_path = os.path.join(project_dir, "docs", "design-tokens.json")
    if os.path.isfile(tokens_path):
        try:
            with open(tokens_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            pass
    return {}

def _load_bdd_scenarios(project_dir: str) -> Dict[str, List[dict]]:
    """Load BDD scenarios and build a map of {req_id: [scenario_dicts]}.

    F-0 v2 (2-layer design): Returns FULL scenario context — not just THEN
    clauses. The LLM code agent needs given/when/then/scenario name to write
    intelligent test assertions. This function is the L1 deterministic helper
    that builds the map; the LLM (L2) writes the actual assertions.

    Tries two sources in order:
      1. docs/bdd-scenarios.json (structured, preferred)
      2. docs/bdd-scenarios.md (Gherkin markdown fallback)

    Returns:
        Dict mapping req_id -> list of scenario context dicts, each with:
          {"scenario": str, "given": str, "when": str, "then": [str]}
        Empty dict if neither file exists or is malformed.
    """
    result: Dict[str, List[dict]] = {}

    # Try structured JSON first
    json_path = os.path.join(project_dir, "docs", "bdd-scenarios.json")
    if os.path.isfile(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Handle both formats: {"scenarios": [...]} or bare [...]
            if isinstance(data, dict):
                scenarios = data.get("scenarios", [])
            elif isinstance(data, list):
                scenarios = data
            else:
                return {}
            for scenario in scenarios:
                req_ids = scenario.get("req_ids", [])
                # Build context dict with full given/when/then
                context = {
                    "scenario": scenario.get("scenario", scenario.get("feature", "")),
                    "given": scenario.get("given", ""),
                    "when": scenario.get("when", ""),
                    "then": scenario.get("then", []),
                }
                for rid in req_ids:
                    if rid not in result:
                        result[rid] = []
                    result[rid].append(context)
            return result
        except (json.JSONDecodeError, IOError, OSError, TypeError, KeyError):
            return {}

    # Fallback: parse Gherkin markdown
    md_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    if os.path.isfile(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                md_content = f.read()
            current_req_ids: List[str] = []
            current_scenario = ""
            current_given = ""
            current_when = ""
            current_thens: List[str] = []

            def _flush_scenario():
                if current_req_ids and current_thens:
                    context = {
                        "scenario": current_scenario,
                        "given": current_given,
                        "when": current_when,
                        "then": list(current_thens),
                    }
                    for rid in current_req_ids:
                        if rid not in result:
                            result[rid] = []
                        result[rid].append(context)

            for line in md_content.split("\n"):
                line_stripped = line.strip()
                # Match scenario headers with REQ-IDs
                scenario_match = re.search(r'\(([^)]+)\)', line_stripped)
                if line_stripped.startswith("### Scenario:") and scenario_match:
                    _flush_scenario()
                    req_str = scenario_match.group(1)
                    current_req_ids = re.findall(r'REQ-\d+', req_str)
                    # Extract scenario name (between "### Scenario:" and "(")
                    name_match = re.match(
                        r'###\s+Scenario:\s*(.+?)\s*\(', line_stripped
                    )
                    current_scenario = name_match.group(1) if name_match else ""
                    current_given = ""
                    current_when = ""
                    current_thens = []
                # Match Given
                given_match = re.match(
                    r'^\*\*Given\*\*\s+(.+)$', line_stripped
                )
                if given_match:
                    current_given = given_match.group(1).strip()
                # Match When
                when_match = re.match(
                    r'^\*\*When\*\*\s+(.+)$', line_stripped
                )
                if when_match:
                    current_when = when_match.group(1).strip()
                # Match Then / And then lines
                then_match = re.match(
                    r'^\*\*(?:And\s+)?[Tt]hen\*\*\s+(.+)$', line_stripped
                )
                if then_match and current_req_ids:
                    current_thens.append(then_match.group(1).strip())

            _flush_scenario()  # flush last scenario
            return result
        except (IOError, OSError):
            pass

    return {}

def _embed_bdd_context(
    scenario: dict,
    lang: str = "typescript",
    dep_graph: dict = None,
    manifest: dict = None,
) -> str:
    """Generate instructional BDD context comments for the LLM code agent.

    F-0 v2 (2-layer design):
      L1 (this function): Embeds structured BDD acceptance criteria as
      comments in the test stub. Provides the full given/when/then context
      and a clear instruction for the code agent.

      L2 (code agent / LLM): Reads these comments and writes the REAL
      test assertions. Only an LLM can understand that "redirected to
      Google review page" means checking for conditional redirect logic
      based on star ratings — regex can't do that.

    Fix C enhancement: When dep_graph and/or manifest are provided,
    appends EXECUTABLE assertions after the comment block:
      - Source 1 (dep_graph): readFileSync + import pattern checks
      - Source 2 (manifest): value-matching assertions (models, emails, domains)
      - Source 3 (structural): common THEN patterns (import, plain-text, link)

    Args:
        scenario: Dict with {scenario, given, when, then: [str]}.
        lang: Target language ('typescript', 'python', or 'universal').
        dep_graph: Optional dependency graph with 'edges' list of
                   {source, target, type} dicts.
        manifest: Optional manifest dict with 'models', 'emails', etc.

    Returns:
        String of comment lines embedding the BDD context + instruction,
        followed by executable assertions when dep_graph/manifest are provided.
    """
    scenario_name = scenario.get("scenario", "")
    given = scenario.get("given", "")
    when = scenario.get("when", "")
    then_clauses = scenario.get("then", [])
    then_count = len(then_clauses)

    lines: List[str] = []

    if lang == "typescript":
        prefix = "    //"
        lines.append(f"{prefix} ═══ BDD ACCEPTANCE CRITERIA ═══")
        if scenario_name:
            lines.append(f"{prefix} Scenario: {scenario_name}")
        lines.append(f"{prefix}   GIVEN {given}")
        lines.append(f"{prefix}   WHEN {when}")
        for clause in then_clauses:
            lines.append(f"{prefix}   THEN {clause}")
        lines.append(f"{prefix}")
        lines.append(
            f"{prefix} 🔴 IMPLEMENT: Write assertions that verify EACH of the "
            f"{then_count} THEN clause(s) above."
        )
        lines.append(
            f"{prefix}    Read the source files and verify the described "
            f"behavior exists in code."
        )
    elif lang == "python":
        prefix = "        #"
        lines.append(f"{prefix} ═══ BDD ACCEPTANCE CRITERIA ═══")
        if scenario_name:
            lines.append(f"{prefix} Scenario: {scenario_name}")
        lines.append(f"{prefix}   GIVEN {given}")
        lines.append(f"{prefix}   WHEN {when}")
        for clause in then_clauses:
            lines.append(f"{prefix}   THEN {clause}")
        lines.append(f"{prefix}")
        lines.append(
            f"{prefix} 🔴 IMPLEMENT: Write assertions that verify EACH of the "
            f"{then_count} THEN clause(s) above."
        )
    else:
        # Universal / markdown
        lines.append("- BDD ACCEPTANCE CRITERIA:")
        if scenario_name:
            lines.append(f"  - Scenario: {scenario_name}")
        lines.append(f"  - GIVEN {given}")
        lines.append(f"  - WHEN {when}")
        for clause in then_clauses:
            lines.append(f"  - THEN {clause}")
        lines.append(
            f"  - 🔴 IMPLEMENT: Write assertions for {then_count} THEN clause(s)"
        )

    # ── Fix C: Append executable assertions from dep_graph, manifest,
    #    and structural patterns ──────────────────────────────────────────
    executable_lines = _generate_executable_assertions(
        then_clauses, lang, dep_graph, manifest,
    )
    if executable_lines:
        lines.append("")  # blank separator between comments and assertions
        lines.extend(executable_lines)

    return "\n".join(lines)


# ─── Fix C helpers ───────────────────────────────────────────────────────────

# Structural THEN patterns matched by keyword
_STRUCTURAL_PATTERNS = [
    # (keyword_regex, ts_assertion_factory, py_assertion_factory)
    # "MUST import X" → import check
    (
        re.compile(r"MUST\s+import\s+(?:the\s+)?(.+?)(?:\s+SDK)?$", re.IGNORECASE),
        lambda m: f"    expect(src).toMatch(/import\\s+.*{re.escape(m.group(1).strip().lower())}/i);",
        lambda m: f"        assert re.search(r'import\\s+.*{re.escape(m.group(1).strip().lower())}', src, re.IGNORECASE)",
    ),
    # "MUST be plain-text" / "no HTML" → HTML tag negative check
    (
        re.compile(r"MUST\s+be\s+plain[-\s]?text|no\s+HTML", re.IGNORECASE),
        lambda _m: "    expect(src).not.toMatch(/<html|<div|<span/i);",
        lambda _m: "        assert not re.search(r'<html|<div|<span', src, re.IGNORECASE)",
    ),
    # "MUST include ... link" with URL → href check
    (
        re.compile(
            r"MUST\s+include\s+(?:a\s+)?link\s+to\s+(https?://\S+)",
            re.IGNORECASE,
        ),
        lambda m: f"    expect(src).toMatch(/href=.*{re.escape(m.group(1))}/i);",
        lambda m: f"        assert re.search(r'href=.*{re.escape(m.group(1))}', src, re.IGNORECASE)",
    ),
    # "MUST be sent from <email>" → toContain email
    (
        re.compile(
            r"MUST\s+be\s+sent\s+from\s+([\w.+-]+@[\w.-]+)",
            re.IGNORECASE,
        ),
        lambda m: f"    expect(src).toContain('{m.group(1)}');",
        lambda m: f"        assert '{m.group(1)}' in src",
    ),
]


def _generate_executable_assertions(
    then_clauses: List[str],
    lang: str,
    dep_graph: Optional[dict],
    manifest: Optional[dict],
) -> List[str]:
    """Generate executable assertion lines from dep_graph, manifest, and patterns.

    Returns a list of code lines (assertions) or empty list if nothing matched.
    """
    assertion_lines: List[str] = []
    is_ts = lang == "typescript"
    is_py = lang == "python"

    # Collect manifest values for fuzzy matching
    manifest_values = _extract_manifest_values(manifest) if manifest else []

    # Collect dep_graph edge info
    edges = (dep_graph or {}).get("edges", []) if dep_graph else []

    for clause in then_clauses:
        clause_lower = clause.lower()

        # ── Source 1: Dependency graph ──
        matched_dep = False
        for edge in edges:
            target_file = edge.get("target", "")
            # Extract module name from path: "src/lib/resend.ts" → "resend"
            target_basename = os.path.splitext(os.path.basename(target_file))[0]
            source_file = edge.get("source", "")

            if target_basename.lower() in clause_lower:
                matched_dep = True
                if is_ts:
                    assertion_lines.append(
                        f"    const src = readFileSync('{source_file}', 'utf-8');"
                    )
                    assertion_lines.append(
                        f"    expect(src).toMatch(/import\\s+.*from\\s+['\"].*{re.escape(target_basename.lower())}/i);"
                    )
                elif is_py:
                    assertion_lines.append(
                        f"        with open('{source_file}') as f:"
                    )
                    assertion_lines.append(
                        f"            src = f.read()"
                    )
                    assertion_lines.append(
                        f"        assert re.search(r'import\\s+.*{re.escape(target_basename.lower())}', src, re.IGNORECASE)"
                    )
                break  # one dep match per clause

        # ── Source 2: Manifest values ──
        matched_manifest = False
        for mv in manifest_values:
            # Normalize for fuzzy matching: "claude-sonnet-4" → "claude sonnet 4"
            mv_normalized = mv.lower().replace("-", " ").replace("_", " ")
            mv_words = mv_normalized.split()
            # Check if all significant words from manifest value appear in clause
            if len(mv_words) >= 1 and all(w in clause_lower for w in mv_words if len(w) > 1):
                matched_manifest = True
                # Build a regex pattern that allows - or _ between words
                regex_parts = [re.escape(w) for w in mv_words if len(w) > 1]
                pattern = r"[-_\s]?".join(regex_parts)
                if is_ts:
                    assertion_lines.append(
                        f"    expect(src).toMatch(/{pattern}/i);"
                    )
                elif is_py:
                    assertion_lines.append(
                        f"        assert re.search(r'{pattern}', src, re.IGNORECASE)"
                    )
                else:
                    # Universal — just note the assertion
                    assertion_lines.append(
                        f"  - ASSERT: source matches pattern /{pattern}/i"
                    )
                break  # one manifest match per clause

        # If manifest match found an email address literal, also emit toContain
        for mv in manifest_values:
            if "@" in mv and mv.lower() in clause_lower:
                if is_ts:
                    assertion_lines.append(
                        f"    expect(src).toContain('{mv}');"
                    )
                elif is_py:
                    assertion_lines.append(
                        f"        assert '{mv}' in src"
                    )
                break

        # ── Source 3: Structural patterns ──
        if not matched_dep and not matched_manifest:
            for pattern_re, ts_factory, py_factory in _STRUCTURAL_PATTERNS:
                m = pattern_re.search(clause)
                if m:
                    if is_ts:
                        assertion_lines.append(ts_factory(m))
                    elif is_py:
                        assertion_lines.append(py_factory(m))
                    else:
                        # Universal — include as markdown assertion
                        assertion_lines.append(
                            f"  - ASSERT: structural check for: {clause}"
                        )
                    break

    return assertion_lines


def _extract_manifest_values(manifest: dict) -> List[str]:
    """Extract searchable values from a manifest dict.

    Pulls model slugs, email addresses, domain names, and other
    string values that might appear in THEN clauses.
    """
    values: List[str] = []
    if not manifest:
        return values

    # Models: extract slug, name, id
    for model in manifest.get("models", []):
        for key in ("slug", "name", "id", "model"):
            v = model.get(key)
            if v and isinstance(v, str):
                values.append(v)

    # Emails
    for email_entry in manifest.get("emails", []):
        if isinstance(email_entry, dict):
            for key in ("address", "email", "from"):
                v = email_entry.get(key)
                if v and isinstance(v, str):
                    values.append(v)
        elif isinstance(email_entry, str):
            values.append(email_entry)

    # Domains
    for domain in manifest.get("domains", []):
        if isinstance(domain, dict):
            v = domain.get("domain") or domain.get("name")
            if v:
                values.append(v)
        elif isinstance(domain, str):
            values.append(domain)

    # Generic string values at top level
    for key in ("api_key_name", "service_name", "provider"):
        v = manifest.get(key)
        if v and isinstance(v, str):
            values.append(v)

    return values


def detect_project_language(project_dir: str) -> str:
    """Detect the primary language/framework of a project.

    ITR-19 Fix: 3-priority detection to support Phase 0 (pre-scaffold).
      Priority 1: content_manifest.json tech_stack (available in Phase 0)
      Priority 2: File markers — package.json, pyproject.toml, setup.py
      Priority 3: "unknown" (no default — let caller decide)

    Args:
        project_dir: Path to the project directory.

    Returns:
        "typescript", "python", or "unknown"
    """
    # Priority 1: content_manifest.json tech_stack (available in Phase 0
    # because the requirements tool creates it before TDD stubs)
    # System 5 (ADR-82): Uses shared parse_manifest() instead of json.load
    manifest = parse_manifest(project_dir)
    tech_stack = manifest.tech_stack
    if tech_stack:
        # tech_stack can be dict {"framework": "Next.js"} or list ["Next.js", "Prisma"]
        if isinstance(tech_stack, dict):
            framework = tech_stack.get("framework", "").lower()
        elif isinstance(tech_stack, list):
            framework = " ".join(str(t).lower() for t in tech_stack)
        else:
            framework = str(tech_stack).lower()
        # JS/TS web frameworks
        if any(kw in framework for kw in ("next", "react", "vue", "nuxt", "vite", "angular", "svelte")):
            return "typescript"
        # Python web frameworks
        if any(kw in framework for kw in ("django", "flask", "fastapi")):
            return "python"
    # Priority 2: File markers (available after scaffold)
    if os.path.exists(os.path.join(project_dir, "package.json")):
        return "typescript"
    if os.path.exists(os.path.join(project_dir, "pyproject.toml")):
        return "python"
    if os.path.exists(os.path.join(project_dir, "setup.py")):
        return "python"
    # Priority 3: No markers — return 'unknown' (NOT 'python')
    # Let the caller decide how to handle unknown language
    return "unknown"

def detect_test_framework(project_dir: str) -> str:
    """Detect the test framework used by a TypeScript/JS project.

    ITR-39 SYSTEM 3: Delegates to project_layout_detector.detect_layout()
    for framework detection, then maps framework → test runner.
    Falls back to package.json dep scan if detect_layout returns unknown.

    Priority order:
      1. project_layout_detector → framework → test runner mapping
      2. package.json dep scan (vitest > jest > mocha)
      3. 'vitest' (default fallback)

    Args:
        project_dir: Path to the project directory.

    Returns:
        'vitest', 'jest', 'mocha', or 'vitest' (default).
    """
    # ITR-39 SYSTEM 3: Delegate to canonical detector
    try:
        from python.helpers.project_layout_detector import detect_layout
        layout = detect_layout(project_dir)
        framework = layout.framework or ""

        # Map framework → test runner via package.json dep check
        if framework != "unknown":
            pkg_path = os.path.join(project_dir, "package.json")
            if os.path.isfile(pkg_path):
                try:
                    with open(pkg_path, "r", encoding="utf-8") as f:
                        pkg = json.load(f)
                    all_deps: Dict[str, str] = {}
                    all_deps.update(pkg.get("dependencies", {}))
                    all_deps.update(pkg.get("devDependencies", {}))
                    if "vitest" in all_deps:
                        return "vitest"
                    if "jest" in all_deps:
                        return "jest"
                    if "mocha" in all_deps:
                        return "mocha"
                except (json.JSONDecodeError, IOError, OSError):
                    pass

            # Framework-based defaults for non-JS projects
            if framework in ("django", "flask", "fastapi", "python"):
                return "pytest"
            if framework == "go":
                return "go test"
            if framework == "rust":
                return "cargo test"
            if framework in ("rails", "sinatra"):
                return "rspec"

            # JS/TS framework without specific test dep → default vitest
            return "vitest"
    except ImportError:
        pass

    # Fallback: original package.json check
    pkg_path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(pkg_path):
        return "vitest"

    try:
        with open(pkg_path, "r", encoding="utf-8") as f:
            pkg = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return "vitest"

    all_deps: Dict[str, str] = {}
    all_deps.update(pkg.get("dependencies", {}))
    all_deps.update(pkg.get("devDependencies", {}))

    if "vitest" in all_deps:
        return "vitest"
    if "jest" in all_deps:
        return "jest"
    if "mocha" in all_deps:
        return "mocha"

    return "vitest"

def _get_test_import_line(framework: str) -> str:
    """Return a comment directing the agent to fill in test imports.

    RCA-465: Removed framework-specific import generation. The LLM knows
    how to write imports for any framework — it just needs to READ the
    project's test config first (vitest.config.ts, jest.config.ts, etc.)
    to determine whether globals mode is enabled.

    Previously this returned rigid imports like:
        import { describe, it, expect } from 'vitest';
    which conflicted with `globals: true` in vitest.config.ts.

    Args:
        framework: 'vitest', 'jest', 'mocha', or any framework name.

    Returns:
        A JS comment directing the agent to fill in correct imports.
    """
    return f"// Test framework: {framework} — agent fills imports based on project config"

def _escape_docstring(text: str) -> str:
    """Escape text for use inside a Python docstring."""
    return text.replace('"""', '\\"\\"\\"').replace("\\", "\\\\")


# ── F-0: Framework Route Convention Resolver ─────────────────────────────
# Maps detected web frameworks to their file-system conventions for route
# handlers and page components. Used by wiring test stubs to generate
# framework-correct path assertions instead of hardcoding one convention.

_ROUTE_CONVENTIONS = {
    # Next.js App Router (default for Next.js >= 13)
    "nextjs_app": {
        "api_route_pattern": "src/app/{segments}/route.{ext}",
        "page_pattern": "src/app/{segments}/page.{ext}",
        "api_ext": "ts",
        "page_ext": "tsx",
        "alt_api_ext": "js",
        "alt_page_ext": "jsx",
    },
    # Next.js Pages Router (< 13 or opt-in)
    "nextjs_pages": {
        "api_route_pattern": "pages/api/{segments}.{ext}",
        "page_pattern": "pages/{segments}.{ext}",
        "api_ext": "ts",
        "page_ext": "tsx",
        "alt_api_ext": "js",
        "alt_page_ext": "jsx",
    },
    # Express.js
    "express": {
        "api_route_pattern": "src/routes/{segments}.{ext}",
        "page_pattern": "src/views/{segments}.{ext}",
        "api_ext": "ts",
        "page_ext": "tsx",
        "alt_api_ext": "js",
        "alt_page_ext": "jsx",
    },
    # Vite + React Router / Vue Router
    "vite": {
        "api_route_pattern": "src/api/{segments}.{ext}",
        "page_pattern": "src/pages/{segments}.{ext}",
        "api_ext": "ts",
        "page_ext": "tsx",
        "alt_api_ext": "js",
        "alt_page_ext": "jsx",
    },
    # Django
    "django": {
        "api_route_pattern": "{segments}/views.py",
        "page_pattern": "{segments}/templates/{segments}.html",
        "api_ext": "py",
        "page_ext": "html",
        "alt_api_ext": "py",
        "alt_page_ext": "html",
    },
    # Flask / FastAPI
    "flask": {
        "api_route_pattern": "app/routes/{segments}.py",
        "page_pattern": "app/templates/{segments}.html",
        "api_ext": "py",
        "page_ext": "html",
        "alt_api_ext": "py",
        "alt_page_ext": "html",
    },
}


def detect_route_convention(project_dir: str) -> dict:
    """Detect the route file convention for the project's framework.

    Returns a convention dict with api_route_pattern, page_pattern, etc.
    Used by wiring test generators to produce framework-correct assertions.

    Detection order:
      1. content_manifest.json tech_stack (highest fidelity)
      2. File markers (src/app/ → App Router, pages/ → Pages Router, etc.)
      3. Fallback to Next.js App Router (most common in AGIX projects)

    Args:
        project_dir: Path to the project directory.

    Returns:
        Convention dict from _ROUTE_CONVENTIONS.
    """
    # Priority 1: Check manifest tech_stack
    manifest = parse_manifest(project_dir)
    tech_stack = manifest.tech_stack
    framework = ""
    if tech_stack:
        if isinstance(tech_stack, dict):
            framework = tech_stack.get("framework", "").lower()
        elif isinstance(tech_stack, list):
            framework = " ".join(str(t).lower() for t in tech_stack)
        else:
            framework = str(tech_stack).lower()

    # Map framework name to convention key
    if "django" in framework:
        return _ROUTE_CONVENTIONS["django"]
    if "flask" in framework or "fastapi" in framework:
        return _ROUTE_CONVENTIONS["flask"]
    if "express" in framework:
        return _ROUTE_CONVENTIONS["express"]
    if "vite" in framework and "next" not in framework:
        return _ROUTE_CONVENTIONS["vite"]

    # Priority 2: For Next.js — detect App Router vs Pages Router
    if os.path.isdir(os.path.join(project_dir, "src", "app")):
        return _ROUTE_CONVENTIONS["nextjs_app"]
    if os.path.isdir(os.path.join(project_dir, "pages")):
        return _ROUTE_CONVENTIONS["nextjs_pages"]

    # Priority 3: Other file markers
    if os.path.isdir(os.path.join(project_dir, "src", "routes")):
        return _ROUTE_CONVENTIONS["express"]
    if os.path.isdir(os.path.join(project_dir, "src", "pages")):
        return _ROUTE_CONVENTIONS["vite"]
    if os.path.isfile(os.path.join(project_dir, "manage.py")):
        return _ROUTE_CONVENTIONS["django"]

    # Default: Next.js App Router
    return _ROUTE_CONVENTIONS["nextjs_app"]



def _parse_navigation_map(nav_text: str) -> dict:
    """Parse a navigation-map.md into structured route data.

    Extracts frontend routes and API endpoints from the markdown format
    produced by build_navigation_map tool. Handles both bullet-list formats:
      - `/path` (METHOD) — Description
      - `METHOD /path` (source_file)

    Args:
        nav_text: Raw markdown content of navigation-map.md.

    Returns:
        Dict with keys:
          - frontend_routes: list of {path, method, description}
          - api_routes: list of {path, method, description}
    """
    result: dict = {"frontend_routes": [], "api_routes": []}
    if not nav_text or not nav_text.strip():
        return result

    # Pattern: - `/path` (METHOD) — Description  OR  - `/path` (METHOD) - Description
    route_pattern = re.compile(
        r'-\s+`(/[^`]*)`\s+\(([^)]+)\)\s*[—-]\s*(.*)',
    )

    # Split into sections by ## headings
    current_section = ""
    for line in nav_text.split("\n"):
        stripped = line.strip()
        # Detect section headings
        if stripped.startswith("## "):
            heading = stripped[3:].strip().lower()
            if "frontend" in heading or "page" in heading:
                current_section = "frontend"
            elif "api" in heading or "endpoint" in heading:
                current_section = "api"
            else:
                current_section = ""
            continue

        # Parse route lines within a section
        match = route_pattern.match(stripped)
        if match and current_section:
            path = match.group(1).strip()
            method = match.group(2).strip()
            description = match.group(3).strip()
            route_entry = {
                "path": path,
                "method": method,
                "description": description,
            }
            if current_section == "frontend":
                result["frontend_routes"].append(route_entry)
            elif current_section == "api":
                result["api_routes"].append(route_entry)

    # Also try table format: | Route | File | Type |
    # Table rows look like: | `/path` | `src/app/path/page.tsx` | page |
    table_pattern = re.compile(
        r'\|\s*`(/[^`]*)`\s*\|',
    )
    current_section = ""
    for line in nav_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip().lower()
            if "frontend" in heading or "page" in heading:
                current_section = "frontend"
            elif "api" in heading or "endpoint" in heading:
                current_section = "api"
            else:
                current_section = ""
            continue
        if "|---" in stripped:
            continue  # Skip table header separator
        table_match = table_pattern.search(stripped)
        if table_match and current_section:
            path = table_match.group(1).strip()
            # Avoid duplicates
            existing_paths = [
                r["path"] for r in result[
                    "frontend_routes" if current_section == "frontend" else "api_routes"
                ]
            ]
            if path not in existing_paths:
                route_entry = {
                    "path": path,
                    "method": "GET" if current_section == "frontend" else "ALL",
                    "description": "",
                }
                if current_section == "frontend":
                    result["frontend_routes"].append(route_entry)
                elif current_section == "api":
                    result["api_routes"].append(route_entry)

    return result
