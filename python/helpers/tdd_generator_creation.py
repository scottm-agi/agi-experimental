"""TDD Generator Creation — stub generation and pipeline functions.

Extracted from the original tdd_generator.py monolith.
Contains all stub generation, writing, and pipeline orchestration.
"""

import glob
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from python.helpers.projects import get_decomp_index_path
from python.helpers.planning_paths import get_path as _planning_path

# System 5 (ADR-82): Direct import — manifest_parser.py is now stable.
from python.helpers.manifest_parser import _find_manifest_path, parse_manifest

logger = logging.getLogger("tdd_generator")

class MissingBDDException(Exception):
    """Raised when a requirement lacks BDD scenarios and literals, preventing TDD generation."""
    pass

from python.helpers.tdd_generator_constants import _DEFERRED_CATEGORIES, _GARBAGE_LITERALS, _SDK_NAMES, _HIDDEN_ELEMENT_FILTER_JS
from python.helpers.tdd_generator_helpers import _extract_route, _extract_sdk_name, _load_design_tokens, _load_bdd_scenarios, _embed_bdd_context, detect_project_language, detect_test_framework, _get_test_import_line, _escape_docstring, _parse_navigation_map, detect_route_convention


def _to_reference_name(filename: str) -> str:
    """Convert a test filename to a reference-spec filename for docs/tdd/.

    AF-4 (ITR-49): Vitest discovers **/*.test.ts by default. Stubs in docs/tdd/
    always fail because they throw 'TODO'. By renaming to .tdd.ts/.tdd.py,
    the test runner ignores them while the executable copies in src/__tests__/
    keep the .test.ts extension.

    Examples:
        test_unit_requirements.test.ts → test_unit_requirements.tdd.ts
        test_design_tokens.test.ts → test_design_tokens.tdd.ts
        test_unit_requirements.py → test_unit_requirements.tdd.py
    """
    import re as _re
    # Replace .test. with .tdd. for TypeScript/JavaScript
    result = _re.sub(r'\.test\.([jt]sx?)$', r'.tdd.\1', filename)
    # Replace .py with .tdd.py for Python test files
    if result == filename and filename.startswith('test_') and filename.endswith('.py'):
        result = filename[:-3] + '.tdd.py'
    return result


def _generate_vitest_config(language: str) -> str:
    """RCA-470 Phase 3.9: Generate vitest.config.ts content.

    Root cause: The code agent spent 15/20 iterations discovering Vitest
    configuration by trial-and-error (path aliases, jsdom, globals). This
    function generates a ready-to-use config so the code agent can run
    tests immediately.

    Args:
        language: Project language ("typescript", "python", "unknown").

    Returns:
        vitest.config.ts content string. Empty string for non-TS projects.
    """
    if language != "typescript":
        return ""

    return """/// <reference types="vitest" />
import { defineConfig } from 'vitest/config';
import tsconfigPaths from 'vite-tsconfig-paths';

export default defineConfig({
  plugins: [tsconfigPaths()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/__tests__/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}', '__tests__/**/*.{test,spec}.{ts,tsx}'],
    css: false,
  },
});
"""

def _write_test_config(project_dir: str, language: str) -> None:
    """Write docs/test-config.json for the RED baseline validator.

    FIX-D (ITR-34): The TDD stub generator knows the project language.
    It writes a simple config so the baseline validator doesn't need
    to hardcode test runner detection.

    Args:
        project_dir: Path to the project root directory.
        language: Detected language ("typescript", "python", or "unknown").
    """
    # Map language to test command and parse format
    if language == "typescript":
        config = {
            "test_command": "npm test -- --verbose --forceExit",
            "parse_format": "node",
            "language": language,
        }
    elif language == "python":
        config = {
            "test_command": "python -m pytest -v --tb=no --no-header",
            "parse_format": "pytest",
            "language": language,
        }
    else:
        # Unknown — don't write config, let validator use heuristics
        return

    docs_dir = os.path.join(project_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    config_path = os.path.join(docs_dir, "test-config.json")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        logger.info(
            "[TDD STUBS] FIX-D: Wrote test-config.json (lang=%s, cmd=%s)",
            language, config["test_command"],
        )
    except (IOError, OSError) as e:
        logger.warning("[TDD STUBS] Could not write test-config.json: %s", e)

def generate_project_readme(manifest: dict) -> str:
    """Generate a project-specific README from a content manifest.

    F-12: Replaces scaffold boilerplate README with project-specific content.
    Guaranteed to NOT contain any scaffold markers (Create Next App, Geist,
    Vercel, bootstrapped with, Learn More, Deploy on, create-next-app).

    Args:
        manifest: Dict with optional keys: project_name, description,
            tech_stack (list), features (list).

    Returns:
        Markdown string with project-specific README content.
    """
    project_name = manifest.get("project_name", "Project")
    description = manifest.get("description", "")
    tech_stack = manifest.get("tech_stack", [])
    features = manifest.get("features", [])

    sections: List[str] = []

    # Title
    sections.append(f"# {project_name}")
    sections.append("")

    # Overview
    sections.append("## Overview")
    sections.append("")
    if description:
        sections.append(description)
    else:
        sections.append(f"{project_name} application.")
    sections.append("")

    # Features
    if features:
        sections.append("## Features")
        sections.append("")
        for feature in features:
            sections.append(f"- {feature}")
        sections.append("")

    # Tech Stack
    if tech_stack:
        sections.append("## Tech Stack")
        sections.append("")
        for tech in tech_stack:
            sections.append(f"- {tech}")
        sections.append("")

    # Getting Started
    sections.append("## Getting Started")
    sections.append("")
    sections.append("### Prerequisites")
    sections.append("")
    sections.append("- Node.js 18+ or Python 3.10+")
    sections.append("- Package manager (npm, yarn, or pip)")
    sections.append("")
    sections.append("### Installation")
    sections.append("")
    sections.append("```bash")
    sections.append("# Install dependencies")
    sections.append("npm install  # or pip install -r requirements.txt")
    sections.append("```")
    sections.append("")
    sections.append("### Running")
    sections.append("")
    sections.append("```bash")
    sections.append("# Start development server")
    sections.append("npm run dev  # or python main.py")
    sections.append("```")
    sections.append("")

    # Development
    sections.append("## Development")
    sections.append("")
    sections.append("```bash")
    sections.append("# Run tests")
    sections.append("npm test  # or pytest")
    sections.append("```")
    sections.append("")

    return "\n".join(sections)

def _generate_typescript_stubs(
    groups: Dict[str, list], reqs: list, project_dir: str,
    framework: str = "vitest",
    bdd_map: Optional[Dict[str, List[dict]]] = None
) -> Dict[str, str]:
    """Generate TypeScript TDD stubs with framework-aware imports.

    F-5: For TypeScript/JS projects detected via package.json.
    FIX-3: Uses detected test framework for imports (vitest/jest/mocha).
    FIX-1: All fs/glob usage uses ESM imports (no CJS require).

    Args:
        groups: Dict mapping test_type -> list of requirement dicts.
        reqs: Full list of requirement dicts.
        project_dir: Path to the project directory.
        framework: Test framework — 'vitest', 'jest', or 'mocha'. Defaults to 'vitest'.
    """
    stubs: Dict[str, str] = {}
    test_import = _get_test_import_line(framework)

    for test_type, group_reqs in groups.items():
        module_name = f"test_{test_type}_requirements.test.ts"

        # RCA-364: Pre-scan group to determine which imports are needed.
        # This prevents ESLint 'no-unused-vars' errors that cause build
        # failures and agent fix loops.
        needs_existsSync = False
        needs_glob_readFile = False
        needs_stripHidden = False
        for _req in group_reqs:
            _cat = _req.get("category", "feature")
            _text = _req.get("text", "")
            _route = _extract_route(_text)
            _lits = [l for l in _req.get("expected_literals", []) if l not in _GARBAGE_LITERALS]
            if _cat in ("page", "compliance") and _route:
                needs_existsSync = True
                needs_glob_readFile = True
            if _lits:
                needs_glob_readFile = True
                needs_stripHidden = True
            if not _lits and _cat not in _DEFERRED_CATEGORIES:
                needs_glob_readFile = True  # structural fallback uses glob

        # Build import lines based on actual usage
        fs_imports = []
        if needs_glob_readFile:
            fs_imports.append("readFileSync")
        if needs_existsSync:
            fs_imports.append("existsSync")
        
        lines = [test_import]
        if fs_imports:
            lines.append(f"import {{ {', '.join(fs_imports)} }} from 'fs';")
        if needs_glob_readFile:
            lines.append("import { globSync } from 'glob';")
        lines.append("")

        # RCA-364: Hoist stripHidden to module scope (once per file)
        # instead of inside every it() block. Prevents massive code
        # duplication and ESLint scoping issues.
        if needs_stripHidden:
            lines.append(f"// SS-6: Strip hidden elements to prevent gaming with hidden divs")
            lines.append(f"{_HIDDEN_ELEMENT_FILTER_JS}")
            lines.append("")

        lines.append(f"describe('{test_type.title().replace('_', ' ')} Requirements', () => {{")

        for req in group_reqs:
            req_id = req.get("req_id", "UNKNOWN")
            # RCA-364: Sanitize test name — escape quotes, replace newlines
            # with spaces, and truncate to 120 chars to prevent:
            #   - Unterminated string literal parse errors
            #   - Excessively long test names
            text = req.get("text", "").replace("\n", " ").replace("\r", " ").replace("'", "\\'")
            if len(text) > 120:
                text = text[:117] + "..."
            suggested = req.get("suggested_test", "")
            raw_literals = req.get("expected_literals", [])
            req_category = req.get("category", "feature")

            # ITR-33 FIX-A3: Deferred categories use it.skip(), not expect(true)
            if not raw_literals and req_category in _DEFERRED_CATEGORIES:
                lines.append(f"  it.skip('{req_id}: {text} [DEFERRED — {req_category}]', () => {{}});")
                lines.append("")
                continue

            # ITR-33 FIX-A1: Filter garbage literals
            useful_literals = [l for l in raw_literals if l not in _GARBAGE_LITERALS]

            lines.append(f"  it('{req_id}: {text}', () => {{")
            if suggested:
                lines.append(f"    // {suggested}")

            # ALWAYS try BDD assertions (structural) — additive with literals
            bdd_scenarios = (bdd_map or {}).get(req_id, [])
            if bdd_scenarios:
                for scn in bdd_scenarios:
                    lines.append(_embed_bdd_context(scn, lang="typescript"))

            # ALSO add literal content assertions if relevant
            if useful_literals:
                lines.append(f"    // Manifest literal verification")
                lines.append(f"    const srcFiles = globSync('src/**/*.{{tsx,ts,jsx,js}}');")
                lines.append(f"    const rawContent = srcFiles.map((f: string) => readFileSync(f, 'utf-8')).join('\\n');")
                lines.append(f"    // SS-6: Filter out hidden elements (stripHidden hoisted above)")
                lines.append(f"    const visibleContent = stripHidden(rawContent);")
                for lit in useful_literals:
                    safe_lit = lit.replace("'", "\\'")
                    lines.append(f"    expect(visibleContent).toContain('{safe_lit}');")
            elif bdd_scenarios:
                # BDD only (no literals) — add throw for implementation
                then_total = sum(len(s.get("then", [])) for s in bdd_scenarios)
                lines.append(f"    throw new Error('BDD: Implement {then_total} assertion(s) for {req_id}');")

            # If neither has content, error
            if not bdd_scenarios and not useful_literals:
                if req_category not in _DEFERRED_CATEGORIES:
                    raise MissingBDDException(f"Missing BDD scenarios for {req_id}")
            lines.append(f"  }});")
            lines.append("")

        lines.append("});")
        lines.append("")

        content = "\n".join(lines)
        stubs[module_name] = content

    # FIX-6: Inject design token assertions for CSS custom properties
    tokens = _load_design_tokens(project_dir)
    if tokens:
        colors = tokens.get("colors", {})
        fonts = tokens.get("fonts", tokens.get("typography", {}))
        if colors or fonts:
            token_lines = [
                test_import,
                "import { readFileSync } from 'fs';",
                "import { globSync } from 'glob';",
                "",
                "describe('Design Token Consumption', () => {",
            ]
            # --- Test 1: CSS custom property checks (original FIX-6) ---
            if colors:
                token_lines.append("  it('globals.css must contain color design token custom properties', () => {")
                token_lines.append("    const cssFiles = globSync('src/**/*.css');")
                token_lines.append("    const cssContent = cssFiles.map(f => readFileSync(f, 'utf-8')).join('\\n');")
                for name in list(colors.keys())[:10]:  # Top 10 color tokens
                    safe_name = name.replace("'", "\\'")
                    token_lines.append(f"    expect(cssContent).toContain('--{safe_name}');")
                token_lines.append("  });")
                token_lines.append("")
            if fonts:
                token_lines.append("  it('globals.css must contain font design token custom properties', () => {")
                token_lines.append("    const cssFiles = globSync('src/**/*.css');")
                token_lines.append("    const cssContent = cssFiles.map(f => readFileSync(f, 'utf-8')).join('\\n');")
                for name in list(fonts.keys())[:5]:  # Top 5 font tokens
                    safe_name = name.replace("'", "\\'")
                    token_lines.append(f"    expect(cssContent).toContain('--font-{safe_name}');")
                token_lines.append("  });")
                token_lines.append("")

            # --- Test 2: Tailwind config extension ---
            if colors:
                color_names = list(colors.keys())[:10]
                token_lines.append("  it('tailwind.config.ts must map design token color names', () => {")
                token_lines.append("    const configPaths = globSync('tailwind.config.{ts,js,mjs,cjs}');")
                token_lines.append("    expect(configPaths.length).toBeGreaterThan(0);")
                token_lines.append("    const configContent = configPaths.map(f => readFileSync(f, 'utf-8')).join('\\n');")
                for name in color_names:
                    safe_name = name.replace("'", "\\'")
                    token_lines.append(f"    expect(configContent).toContain('{safe_name}');")
                token_lines.append("  });")
                token_lines.append("")

            # --- Test 3: Component consumption (TSX files use token classes) ---
            if colors:
                color_names = list(colors.keys())[:10]
                token_lines.append("  it('TSX components must use design token Tailwind classes', () => {")
                token_lines.append("    const tsxFiles = globSync('src/**/*.tsx');")
                token_lines.append("    const tsxContent = tsxFiles.map(f => readFileSync(f, 'utf-8')).join('\\n');")
                token_lines.append("    // At least some token colors must appear as bg-<token> or text-<token>")
                token_lines.append("    const tokenPatterns = [")
                for name in color_names:
                    safe_name = name.replace("'", "\\'")
                    token_lines.append(f"      /(?:bg|text|border|ring)-{safe_name}/,")
                token_lines.append("    ];")
                token_lines.append("    const matchCount = tokenPatterns.filter(p => p.test(tsxContent)).length;")
                token_lines.append("    expect(matchCount).toBeGreaterThan(0);")
                token_lines.append("  });")
                token_lines.append("")

            # --- Test 4: Default Tailwind class rejection ---
            if colors:
                token_lines.append("  it('TSX files must NOT use default Tailwind color classes when design tokens exist', () => {")
                token_lines.append("    const tsxFiles = globSync('src/**/*.tsx');")
                token_lines.append("    const tsxContent = tsxFiles.map(f => readFileSync(f, 'utf-8')).join('\\n');")
                token_lines.append("    // Banned default color families — use design tokens instead")
                token_lines.append("    const bannedFamilies = ['slate', 'gray', 'zinc', 'neutral', 'stone'];")
                token_lines.append("    for (const family of bannedFamilies) {")
                token_lines.append("      const pattern = new RegExp(`(?:bg|text|border)-${family}-\\d`, 'g');")
                token_lines.append("      const matches = tsxContent.match(pattern) || [];")
                token_lines.append("      expect(matches.length).toBe(0);")
                token_lines.append("    }")
                token_lines.append("  });")
                token_lines.append("")

            # --- Test 5: Theme consistency across pages ---
            if colors:
                token_lines.append("  it('all page.tsx files must use consistent theme/color mode', () => {")
                token_lines.append("    const pageFiles = globSync('src/**/page.tsx');")
                token_lines.append("    if (pageFiles.length === 0) return;")
                token_lines.append("    // Check that no page mixes dark/light mode classes inconsistently")
                token_lines.append("    const darkPages: string[] = [];")
                token_lines.append("    const lightPages: string[] = [];")
                token_lines.append("    for (const pf of pageFiles) {")
                token_lines.append("      const content = readFileSync(pf, 'utf-8');")
                token_lines.append("      if (/dark:/.test(content)) darkPages.push(pf);")
                token_lines.append("      else lightPages.push(pf);")
                token_lines.append("    }")
                token_lines.append("    // All pages should be same mode — either all dark-aware or all light-only")
                token_lines.append("    const consistent = darkPages.length === 0 || lightPages.length === 0 || darkPages.length === pageFiles.length;")
                token_lines.append("    expect(consistent).toBe(true);")
                token_lines.append("  });")
                token_lines.append("")

            # --- Test 6: Nav uniqueness (only layout has <nav>) ---
            token_lines.append("  it('nav elements must only appear in layout.tsx, not page.tsx', () => {")
            token_lines.append("    const pageFiles = globSync('src/**/page.tsx');")
            token_lines.append("    for (const pf of pageFiles) {")
            token_lines.append("      const content = readFileSync(pf, 'utf-8');")
            token_lines.append("      expect(content).not.toMatch(/<nav[\\s>]/);")
            token_lines.append("    }")
            token_lines.append("    // Nav should exist in layout.tsx files")
            token_lines.append("    const layoutFiles = globSync('src/**/layout.tsx');")
            token_lines.append("    if (layoutFiles.length > 0) {")
            token_lines.append("      const layoutContent = layoutFiles.map(f => readFileSync(f, 'utf-8')).join('\\n');")
            token_lines.append("      expect(layoutContent).toMatch(/<nav[\\s>]/);")
            token_lines.append("    }")
            token_lines.append("  });")
            token_lines.append("")

            token_lines.append("});")
            token_lines.append("")
            stubs["test_design_tokens.test.ts"] = "\n".join(token_lines)
            logger.info(
                f"[TDD STUBS] FIX-6: Generated design token test module "
                f"({len(colors)} colors, {len(fonts)} fonts)"
            )

    # Write to docs/tdd/ — AF-4: use .tdd.ts extension to prevent vitest discovery
    stubs_dir = os.path.join(project_dir, "docs", "tdd")
    os.makedirs(stubs_dir, exist_ok=True)
    for module_name, content in stubs.items():
        ref_name = _to_reference_name(module_name)
        stub_path = os.path.join(stubs_dir, ref_name)
        with open(stub_path, "w") as f:
            f.write(content)

    logger.info(
        f"[TDD STUBS] Generated {len(stubs)} TypeScript test modules with "
        f"{len(reqs)} test functions in docs/tdd/"
    )
    return stubs

def _generate_python_stubs(
    groups: Dict[str, list], reqs: list, project_dir: str,
    bdd_map: Optional[Dict[str, List[dict]]] = None
) -> Dict[str, str]:
    """Generate Python/unittest TDD stubs (existing behavior)."""
    stubs: Dict[str, str] = {}

    for test_type, group_reqs in groups.items():
        module_name = f"test_{test_type}_requirements.py"
        lines = [
            '"""',
            f"Auto-generated TDD stubs for {test_type} requirements.",
            "",
            "Generated by skeleton_generator.generate_tdd_tests().",
            "Each test function is linked to a requirement ID.",
            "Implement the test body to make them pass.",
            '"""',
            "",
            "import unittest",
            "",
            "",
            f"class Test{test_type.title().replace('_', '')}Requirements(unittest.TestCase):",
            f'    """TDD stubs for {test_type} requirements."""',
            "",
        ]

        for req in group_reqs:
            req_id = req.get("req_id", "UNKNOWN")
            text = req.get("text", "")
            suggested = req.get("suggested_test", "")
            literals = req.get("expected_literals", [])
            criteria = req.get("acceptance_criteria", "")

            func_name = req_id.lower().replace("-", "_")

            lines.append(f"    def test_{func_name}(self):")
            lines.append(f'        """{req_id}: {_escape_docstring(text)}')
            if suggested:
                lines.append(f"")
                lines.append(f"        {_escape_docstring(suggested)}")
            if criteria:
                lines.append(f"")
                lines.append(f"        Acceptance: {_escape_docstring(criteria[:120])}")
            lines.append(f'        """')

            if literals:
                # ITR-42 Fix 2: Generate EXECUTABLE assertions, not comments.
                # Each literal from content_manifest.json becomes a real
                # self.assertIn() call. This ensures tests FAIL if the code
                # agent writes wrong values (e.g., $199 instead of $200).
                lines.append(f"        import glob as _glob")
                lines.append(f"        _src_files = _glob.glob('src/**/*.*', recursive=True)")
                lines.append(f"        _content = ''")
                lines.append(f"        for _f in _src_files:")
                lines.append(f"            try:")
                lines.append(f"                with open(_f, 'r', encoding='utf-8', errors='ignore') as _fh:")
                lines.append(f"                    _content += _fh.read()")
                lines.append(f"            except (IOError, OSError):")
                lines.append(f"                pass")
                for lit in literals:
                    safe_lit = lit.replace("'", "\\'")
                    lines.append(f"        self.assertIn('{safe_lit}', _content, 'Manifest literal missing from source: {safe_lit}')")
            else:
                # F-0 v2: Embed BDD context as instructions for LLM code agent
                bdd_scenarios = (bdd_map or {}).get(req_id, [])
                if bdd_scenarios:
                    for scn in bdd_scenarios:
                        lines.append(_embed_bdd_context(scn, lang="python"))
                    then_total = sum(len(s.get("then", [])) for s in bdd_scenarios)
                    lines.append(f"        raise NotImplementedError('BDD: Implement {then_total} assertion(s) for {req_id}')")
                else:
                    # ITR-32 F-6: Deferred categories get DEFERRED, not TODO
                    req_category = req.get("category", "feature")
                    if req_category in _DEFERRED_CATEGORIES:
                        lines.append(f"        # DEFERRED: {req_id} — {req_category} category (out-of-scope for Phase 3)")
                        lines.append(f"        pass  # Placeholder — implement when {req_category} phase begins")
                    else:
                        # RCA-366: No fallbacks. Throw exception to force BDD generation retry.
                        raise MissingBDDException(f"Missing BDD scenarios for {req_id}")
            lines.append("")

        lines.append("")
        lines.append("if __name__ == '__main__':")
        lines.append("    unittest.main()")
        lines.append("")

        content = "\n".join(lines)
        stubs[module_name] = content

    # FIX-6: Inject design token assertions for CSS custom properties (Python)
    tokens = _load_design_tokens(project_dir)
    if tokens:
        colors = tokens.get("colors", {})
        fonts = tokens.get("fonts", tokens.get("typography", {}))
        if colors or fonts:
            token_lines = [
                '"""',
                "Auto-generated TDD stubs for design token consumption.",
                "",
                "FIX-6: Verifies CSS custom properties from design-tokens.json.",
                '"""',
                "",
                "import glob",
                "import unittest",
                "",
                "",
                "class TestDesignTokenConsumption(unittest.TestCase):",
                '    """Verify design tokens appear as CSS custom properties."""',
                "",
                "    def _read_all_css(self):",
                "        css_files = glob.glob('src/**/*.css', recursive=True)",
                "        content = ''",
                "        for f in css_files:",
                "            try:",
                "                with open(f, 'r', encoding='utf-8', errors='ignore') as fh:",
                "                    content += fh.read()",
                "            except (IOError, OSError):",
                "                pass",
                "        return content",
                "",
            ]
            if colors:
                token_lines.append("    def test_color_design_tokens_in_css(self):")
                token_lines.append('        """CSS must contain color design token custom properties."""')
                token_lines.append("        css_content = self._read_all_css()")
                for name in list(colors.keys())[:10]:
                    safe_name = name.replace("'", "\\\'")
                    token_lines.append(f"        self.assertIn('--{safe_name}', css_content, 'Missing color token: --{safe_name}')")
                token_lines.append("")
            if fonts:
                token_lines.append("    def test_font_design_tokens_in_css(self):")
                token_lines.append('        """CSS must contain font design token custom properties."""')
                token_lines.append("        css_content = self._read_all_css()")
                for name in list(fonts.keys())[:5]:
                    safe_name = name.replace("'", "\\\'")
                    token_lines.append(f"        self.assertIn('--font-{safe_name}', css_content, 'Missing font token: --font-{safe_name}')")
                token_lines.append("")

            # --- Helper: read all TSX/py files ---
            token_lines.append("    def _read_all_tsx(self):")
            token_lines.append("        tsx_files = glob.glob('src/**/*.tsx', recursive=True)")
            token_lines.append("        content = ''")
            token_lines.append("        for f in tsx_files:")
            token_lines.append("            try:")
            token_lines.append("                with open(f, 'r', encoding='utf-8', errors='ignore') as fh:")
            token_lines.append("                    content += fh.read()")
            token_lines.append("            except (IOError, OSError):")
            token_lines.append("                pass")
            token_lines.append("        return content")
            token_lines.append("")

            # --- Test 2: Tailwind config extension ---
            if colors:
                color_names = list(colors.keys())[:10]
                token_lines.append("    def test_tailwind_config_maps_token_colors(self):")
                token_lines.append('        """tailwind.config.ts must map design token color names."""')
                token_lines.append("        config_files = glob.glob('tailwind.config.*')")
                token_lines.append("        self.assertGreater(len(config_files), 0, 'No tailwind.config.* found')")
                token_lines.append("        config_content = ''")
                token_lines.append("        for f in config_files:")
                token_lines.append("            with open(f, 'r', encoding='utf-8', errors='ignore') as fh:")
                token_lines.append("                config_content += fh.read()")
                for name in color_names:
                    safe_name = name.replace("'", "\\'")
                    token_lines.append(f"        self.assertIn('{safe_name}', config_content, 'Token color {safe_name} not in tailwind config')")
                token_lines.append("")

            # --- Test 3: Component consumption ---
            if colors:
                color_names = list(colors.keys())[:10]
                token_lines.append("    def test_tsx_components_use_token_classes(self):")
                token_lines.append('        """TSX components must use design token Tailwind classes (bg-<token> or text-<token>)."""')
                token_lines.append("        import re")
                token_lines.append("        tsx_content = self._read_all_tsx()")
                token_lines.append("        match_count = 0")
                for name in color_names:
                    safe_name = name.replace("'", "\\'")
                    token_lines.append(f"        if re.search(r'(?:bg|text|border|ring)-{safe_name}', tsx_content):")
                    token_lines.append(f"            match_count += 1")
                token_lines.append("        self.assertGreater(match_count, 0, 'No TSX files use design token Tailwind classes')")
                token_lines.append("")

            # --- Test 4: Default Tailwind class rejection ---
            if colors:
                token_lines.append("    def test_no_default_tailwind_color_classes(self):")
                token_lines.append('        """TSX files must NOT use default Tailwind color classes (slate, gray, zinc, neutral, stone)."""')
                token_lines.append("        import re")
                token_lines.append("        tsx_content = self._read_all_tsx()")
                token_lines.append("        banned = ['slate', 'gray', 'zinc', 'neutral', 'stone']")
                token_lines.append("        for family in banned:")
                token_lines.append("            matches = re.findall(rf'(?:bg|text|border)-{family}-\\d', tsx_content)")
                token_lines.append("            self.assertEqual(len(matches), 0, f'Found banned default class: {family}')")
                token_lines.append("")

            # --- Test 5: Theme consistency ---
            if colors:
                token_lines.append("    def test_theme_consistency_across_pages(self):")
                token_lines.append('        """All page.tsx files must use consistent theme/color mode."""')
                token_lines.append("        import re")
                token_lines.append("        page_files = glob.glob('src/**/page.tsx', recursive=True)")
                token_lines.append("        if not page_files:")
                token_lines.append("            return  # No pages to check")
                token_lines.append("        dark_pages = []")
                token_lines.append("        light_pages = []")
                token_lines.append("        for pf in page_files:")
                token_lines.append("            with open(pf, 'r', encoding='utf-8', errors='ignore') as fh:")
                token_lines.append("                content = fh.read()")
                token_lines.append("            if re.search(r'dark:', content):")
                token_lines.append("                dark_pages.append(pf)")
                token_lines.append("            else:")
                token_lines.append("                light_pages.append(pf)")
                token_lines.append("        consistent = len(dark_pages) == 0 or len(light_pages) == 0 or len(dark_pages) == len(page_files)")
                token_lines.append("        self.assertTrue(consistent, 'Inconsistent dark/light mode across page.tsx files')")
                token_lines.append("")

            # --- Test 6: Nav uniqueness ---
            token_lines.append("    def test_nav_only_in_layout(self):")
            token_lines.append('        """Nav elements must only appear in layout.tsx, not page.tsx."""')
            token_lines.append("        import re")
            token_lines.append("        page_files = glob.glob('src/**/page.tsx', recursive=True)")
            token_lines.append("        for pf in page_files:")
            token_lines.append("            with open(pf, 'r', encoding='utf-8', errors='ignore') as fh:")
            token_lines.append("                content = fh.read()")
            token_lines.append("            self.assertIsNone(re.search(r'<nav[\\s>]', content), f'Found <nav> in {pf}')")
            token_lines.append("        layout_files = glob.glob('src/**/layout.tsx', recursive=True)")
            token_lines.append("        if layout_files:")
            token_lines.append("            layout_content = ''")
            token_lines.append("            for lf in layout_files:")
            token_lines.append("                with open(lf, 'r', encoding='utf-8', errors='ignore') as fh:")
            token_lines.append("                    layout_content += fh.read()")
            token_lines.append("            self.assertRegex(layout_content, r'<nav[\\s>]', 'No <nav> found in layout.tsx')")
            token_lines.append("")

            token_lines.append("")
            token_lines.append("if __name__ == '__main__':")
            token_lines.append("    unittest.main()")
            token_lines.append("")
            stubs["test_design_tokens.py"] = "\n".join(token_lines)
            logger.info(
                f"[TDD STUBS] FIX-6: Generated Python design token test module "
                f"({len(colors)} colors, {len(fonts)} fonts)"
            )

    # Write to docs/tdd/ — AF-4: use .tdd.py extension to prevent pytest discovery
    stubs_dir = os.path.join(project_dir, "docs", "tdd")
    os.makedirs(stubs_dir, exist_ok=True)
    for module_name, content in stubs.items():
        ref_name = _to_reference_name(module_name)
        stub_path = os.path.join(stubs_dir, ref_name)
        with open(stub_path, "w") as f:
            f.write(content)

    logger.info(
        f"[TDD STUBS] Generated {len(stubs)} Python test modules with "
        f"{len(reqs)} test functions in docs/tdd/"
    )
    return stubs

def _generate_universal_stubs(
    groups: Dict[str, list], reqs: list, project_dir: str,
    bdd_map: Optional[Dict[str, List[dict]]] = None
) -> Dict[str, str]:
    """Generate language-agnostic TDD stubs (pseudocode format).

    ITR-19: Used when language is 'unknown' (no manifest tech_stack, no
    file markers). Produces human-readable test descriptions that the
    code agent will convert to the project's actual language.

    This is a fallback — the LLM is the final arbiter of test syntax.
    """
    stubs: Dict[str, str] = {}

    for test_type, group_reqs in groups.items():
        module_name = f"test_{test_type}_requirements.md"
        lines = [
            f"# TDD Test Stubs: {test_type.title().replace('_', ' ')} Requirements",
            "",
            "Language: UNKNOWN — convert to the project's test framework.",
            "Each test maps to a requirement ID from test-skeleton.json.",
            "",
        ]

        for req in group_reqs:
            req_id = req.get("req_id", "UNKNOWN")
            text = req.get("text", "")
            suggested = req.get("suggested_test", "")
            literals = req.get("expected_literals", [])
            criteria = req.get("acceptance_criteria", "")

            lines.append(f"## {req_id}: {text}")
            if suggested:
                lines.append(f"- Test: {suggested}")
            if criteria:
                lines.append(f"- Acceptance: {criteria[:200]}")
            if literals:
                lines.append(f"- Expected literals: {literals}")
            # F-0 v2: BDD context for universal stubs
            bdd_scenarios = (bdd_map or {}).get(req_id, [])
            if bdd_scenarios:
                for scn in bdd_scenarios:
                    lines.append(_embed_bdd_context(scn, lang="universal"))
            # ITR-32 F-6: Deferred categories
            req_category = req.get("category", "feature")
            if req_category in _DEFERRED_CATEGORIES:
                lines.append(f"- Status: DEFERRED — {req_category} category (out-of-scope for Phase 3)")
            elif not bdd_scenarios:
                lines.append(f"- Status: TODO — implement in project language")
            lines.append("")

        content = "\n".join(lines)
        stubs[module_name] = content

    # Write to docs/tdd/ — AF-4: use .tdd. extension for reference specs
    stubs_dir = os.path.join(project_dir, "docs", "tdd")
    os.makedirs(stubs_dir, exist_ok=True)
    for module_name, content in stubs.items():
        ref_name = _to_reference_name(module_name)
        stub_path = os.path.join(stubs_dir, ref_name)
        with open(stub_path, "w") as f:
            f.write(content)

    logger.info(
        f"[TDD STUBS] Generated {len(stubs)} universal (language-agnostic) test stubs "
        f"with {len(reqs)} test descriptions in docs/tdd/"
    )
    return stubs

def _write_stubs_to_test_dir(
    stubs: Dict[str, str], project_dir: str, language: str
) -> List[str]:
    """Copy TDD stubs to the project's test runner directory.

    F-6: The test runner auto-discovers tests from conventional directories
    (src/__tests__/ or __tests__/ for TypeScript, tests/ for Python).
    Stubs in docs/tdd/ are invisible to the runner, so we copy them.

    C-1: After writing each stub file, computes MD5 and stores in
    .stub_checksums.json for downstream integrity verification.

    Args:
        stubs: Dict of filename → content (from language-specific generator).
        project_dir: Path to the project directory.
        language: "typescript", "python", or "unknown".

    Returns:
        List of absolute paths written to the test directory.
        Empty list for unknown language (no convention to target).
    """
    if language == "unknown":
        return []

    # Determine test directory
    if language == "typescript":
        # Prefer src/__tests__/ if src/ exists, else __tests__/ at root
        src_dir = os.path.join(project_dir, "src")
        if os.path.isdir(src_dir):
            test_dir = os.path.join(src_dir, "__tests__")
        else:
            test_dir = os.path.join(project_dir, "__tests__")
    elif language == "python":
        test_dir = os.path.join(project_dir, "tests")
    else:
        return []

    os.makedirs(test_dir, exist_ok=True)
    written_paths: List[str] = []
    checksums: Dict[str, str] = {}

    for filename, content in stubs.items():
        dest_path = os.path.join(test_dir, filename)
        with open(dest_path, "w") as f:
            f.write(content)
        written_paths.append(dest_path)
        # C-1: Compute MD5 checksum of the original content
        checksums[filename] = hashlib.md5(content.encode("utf-8")).hexdigest()

    # C-1: Write checksums file for integrity verification
    checksums_path = os.path.join(test_dir, ".stub_checksums.json")
    try:
        with open(checksums_path, "w", encoding="utf-8") as f:
            json.dump(checksums, f, indent=2)
        logger.info(
            "[TDD STUBS] C-1: Wrote .stub_checksums.json with %d entries",
            len(checksums),
        )
    except (IOError, OSError) as e:
        logger.warning("[TDD STUBS] C-1: Could not write checksums: %s", e)

    logger.info(
        f"[TDD STUBS] F-6: Copied {len(written_paths)} stubs to "
        f"{os.path.relpath(test_dir, project_dir)}/ for test runner auto-discovery"
    )

    # ADR-086 Phase 3 Step 3-2: Set TDD stage status for REQ-IDs in stubs.
    # Scan stub content for REQ-ID patterns and update the on-disk ledger
    # to mark tdd stage as completed. This is a disk-based update since
    # _write_stubs_to_test_dir doesn't have access to agent_data.
    try:
        import re
        from python.helpers.requirements_ledger import set_stage_status

        # Extract REQ-IDs from all stub content
        tdd_req_ids = set()
        for content in stubs.values():
            found = re.findall(r"REQ-[a-f0-9]+", content, re.IGNORECASE)
            tdd_req_ids.update(found)

        if tdd_req_ids:
            ledger_path = _planning_path(project_dir, "requirements_ledger")
            if os.path.isfile(ledger_path):
                with open(ledger_path, "r", encoding="utf-8") as lf:
                    ledger_data = json.load(lf)
                tdd_stage_count = 0
                for req in ledger_data.get("requirements", []):
                    if req.get("id") in tdd_req_ids:
                        set_stage_status(req, "tdd", "completed")
                        tdd_stage_count += 1
                if tdd_stage_count > 0:
                    with open(ledger_path, "w", encoding="utf-8") as lf:
                        json.dump(ledger_data, lf, indent=2, ensure_ascii=False)
                    logger.info(
                        "[TDD STUBS] ADR-086: Set tdd stage to 'completed' "
                        "for %d requirements", tdd_stage_count
                    )
    except Exception as stage_err:
        logger.debug("[TDD STUBS] ADR-086: TDD stage update skipped: %s", stage_err)

    return written_paths

def verify_stub_integrity(
    project_dir: str,
) -> tuple:
    """Verify deployed test stubs match their original checksums.

    C-1: Compares each file listed in .stub_checksums.json against
    the actual file on disk. Detects mutations and deletions.

    Searches for .stub_checksums.json in conventional test directories:
    - tests/ (Python)
    - src/__tests__/ (TypeScript with src/)
    - __tests__/ (TypeScript without src/)

    Args:
        project_dir: Path to the project root directory.

    Returns:
        (valid: bool, problems: list[str])
        - valid=True, problems=[] when all stubs intact or no checksums exist
        - valid=False, problems=['filename', 'filename (DELETED)'] on issues
    """
    # Search for checksums file in conventional locations
    candidate_dirs = [
        os.path.join(project_dir, "tests"),
        os.path.join(project_dir, "src", "__tests__"),
        os.path.join(project_dir, "__tests__"),
    ]

    checksums_path = None
    test_dir = None
    for d in candidate_dirs:
        candidate = os.path.join(d, ".stub_checksums.json")
        if os.path.isfile(candidate):
            checksums_path = candidate
            test_dir = d
            break

    if checksums_path is None:
        # No checksums file — nothing to verify
        return (True, [])

    try:
        with open(checksums_path, "r", encoding="utf-8") as f:
            checksums = json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning("[TDD STUBS] C-1: Could not read checksums: %s", e)
        return (True, [])

    problems: List[str] = []

    for filename, expected_md5 in checksums.items():
        file_path = os.path.join(test_dir, filename)
        if not os.path.isfile(file_path):
            problems.append(f"{filename} (DELETED)")
            continue

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        actual_md5 = hashlib.md5(content.encode("utf-8")).hexdigest()

        if actual_md5 != expected_md5:
            problems.append(filename)

    valid = len(problems) == 0
    if not valid:
        logger.warning(
            "[TDD STUBS] C-1: Stub integrity check FAILED: %s", problems
        )
    else:
        logger.info("[TDD STUBS] C-1: Stub integrity check PASSED (%d files)", len(checksums))

    return (valid, problems)

def generate_wiring_test_stubs(project_dir: str) -> Dict[str, str]:
    """Generate TDD test stubs for API route completeness (wiring tests).

    Reads docs/navigation-map.md (and optionally docs/decomposition_index.json)
    to extract all frontend routes and API endpoints, then generates test stubs
    that verify:
      - Every API endpoint has a matching route handler
      - Every frontend route has a page component

    Language-aware: generates vitest (TypeScript) or unittest (Python) format.

    Stubs are written to docs/tdd/ and copied to the test runner directory
    (src/__tests__/ for TypeScript, tests/ for Python) for auto-discovery.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        Dict mapping filename -> file content for generated stub files.
        Empty dict if no navigation map exists or no routes found.
    """
    # Read navigation-map.md
    nav_map_path = os.path.join(project_dir, "docs", "navigation-map.md")
    if not os.path.isfile(nav_map_path):
        logger.info("[WIRING STUBS] No navigation-map.md found, skipping")
        return {}

    with open(nav_map_path, "r", encoding="utf-8") as f:
        nav_text = f.read()

    parsed = _parse_navigation_map(nav_text)
    frontend_routes = parsed.get("frontend_routes", [])
    api_routes = parsed.get("api_routes", [])

    # Optionally enrich from decomposition_index.json
    decomp_path = get_decomp_index_path(project_dir)
    if os.path.isfile(decomp_path):
        try:
            with open(decomp_path, "r", encoding="utf-8") as f:
                decomp = json.load(f)
            if isinstance(decomp, list):
                api_pattern = re.compile(r'(GET|POST|PUT|DELETE|PATCH|ALL)\s+(/api/[^\s,)]+)', re.IGNORECASE)
                existing_api_paths = {r["path"] for r in api_routes}
                for item in decomp:
                    desc = item.get("description", "") + " " + item.get("title", "")
                    for match in api_pattern.finditer(desc):
                        method = match.group(1).upper()
                        path = match.group(2)
                        if path not in existing_api_paths:
                            api_routes.append({
                                "path": path,
                                "method": method,
                                "description": item.get("title", ""),
                            })
                            existing_api_paths.add(path)
        except (json.JSONDecodeError, IOError, OSError):
            pass

    # If no routes found at all, return empty
    if not frontend_routes and not api_routes:
        return {}

    # Detect project language
    language = detect_project_language(project_dir)

    # FIX-3: Detect test framework for framework-aware imports
    test_framework = detect_test_framework(project_dir)

    stubs: Dict[str, str] = {}

    # F-0: Detect framework route convention for universal path assertions
    convention = detect_route_convention(project_dir)

    if language == "typescript" or language == "unknown":
        stubs.update(_generate_wiring_typescript_stubs(frontend_routes, api_routes, framework=test_framework, convention=convention))
    elif language == "python":
        stubs.update(_generate_wiring_python_stubs(frontend_routes, api_routes, convention=convention))

    # Write to docs/tdd/
    stubs_dir = os.path.join(project_dir, "docs", "tdd")
    os.makedirs(stubs_dir, exist_ok=True)
    for filename, content in stubs.items():
        stub_path = os.path.join(stubs_dir, filename)
        with open(stub_path, "w") as f_out:
            f_out.write(content)

    # Copy to test runner directory
    test_dir_paths = _write_stubs_to_test_dir(stubs, project_dir, language)
    if test_dir_paths:
        logger.info(
            f"[WIRING STUBS] Copied {len(test_dir_paths)} wiring stubs to test runner dir"
        )

    logger.info(
        f"[WIRING STUBS] Generated {len(stubs)} wiring test stubs "
        f"({len(api_routes)} API routes, {len(frontend_routes)} frontend routes)"
    )
    return stubs

def _generate_wiring_typescript_stubs(
    frontend_routes: List[dict], api_routes: List[dict],
    framework: str = "vitest",
    convention: dict = None,
) -> Dict[str, str]:
    """Generate TypeScript wiring test stubs with framework-aware imports.

    FIX-3: Uses detected test framework for imports (vitest/jest/mocha).
    F-0: Uses detected route convention for framework-correct paths.

    Produces test stubs that verify:
      - Each API endpoint has a route handler file
      - Each frontend route has a page component
    """
    if convention is None:
        convention = {"api_route_pattern": "src/app/{segments}/route.{ext}",
                      "page_pattern": "src/app/{segments}/page.{ext}",
                      "api_ext": "ts", "page_ext": "tsx",
                      "alt_api_ext": "js", "alt_page_ext": "jsx"}

    test_import = _get_test_import_line(framework)
    lines = [
        test_import,
        "import { readdirSync, existsSync } from 'fs';",
        "import path from 'path';",
        "",
    ]

    # API Route Completeness
    if api_routes:
        lines.append("describe('API Route Completeness', () => {")
        for route in api_routes:
            rpath = route["path"]
            desc = route.get("description", "")
            safe_path = rpath.replace("'", "\\'")
            comment = f" // {desc}" if desc else ""
            # Build path from convention pattern
            segments = [s for s in rpath.strip("/").split("/") if s]
            # Resolve API route path from convention
            api_pattern = convention["api_route_pattern"]
            ext = convention["api_ext"]
            alt_ext = convention["alt_api_ext"]
            if "{segments}" in api_pattern:
                segment_str = ", ".join(f"'{s}'" for s in segments)
                # Split pattern on {segments} to get prefix and suffix
                prefix, suffix = api_pattern.split("{segments}", 1)
                prefix_parts = [p for p in prefix.strip("/").split("/") if p]
                suffix_parts = suffix.replace("{ext}", ext).strip("/").split("/") if suffix.strip("/") else []
                alt_suffix_parts = suffix.replace("{ext}", alt_ext).strip("/").split("/") if suffix.strip("/") else []
                all_parts = prefix_parts + segments + ([suffix_parts[-1]] if suffix_parts else [])
                all_parts_alt = prefix_parts + segments + ([alt_suffix_parts[-1]] if alt_suffix_parts else [])
                part_str = ", ".join(f"'{p}'" for p in all_parts)
                alt_part_str = ", ".join(f"'{p}'" for p in all_parts_alt)
            else:
                part_str = f"'{rpath.strip('/')}'"
                alt_part_str = part_str
            lines.append(f"  it('has handler for {safe_path}', () => {{{comment}")
            lines.append(f"    const routePath = path.join(process.cwd(), {part_str});")
            lines.append(f"    const altPath = path.join(process.cwd(), {alt_part_str});")
            lines.append(f"    expect(existsSync(routePath) || existsSync(altPath)).toBe(true);")
            lines.append("  });")
            lines.append("")
        lines.append("});")
        lines.append("")

    # Frontend Route Accessibility
    if frontend_routes:
        lines.append("describe('Frontend Route Accessibility', () => {")
        for route in frontend_routes:
            rpath = route["path"]
            desc = route.get("description", "")
            safe_path = rpath.replace("'", "\\'")
            comment = f" // {desc}" if desc else ""
            segments = [s for s in rpath.strip("/").split("/") if s]
            # Resolve page path from convention
            page_pattern = convention["page_pattern"]
            ext = convention["page_ext"]
            alt_ext = convention["alt_page_ext"]
            if "{segments}" in page_pattern:
                prefix, suffix = page_pattern.split("{segments}", 1)
                prefix_parts = [p for p in prefix.strip("/").split("/") if p]
                suffix_parts = suffix.replace("{ext}", ext).strip("/").split("/") if suffix.strip("/") else []
                alt_suffix_parts = suffix.replace("{ext}", alt_ext).strip("/").split("/") if suffix.strip("/") else []
                all_parts = prefix_parts + segments + ([suffix_parts[-1]] if suffix_parts else [])
                all_parts_alt = prefix_parts + segments + ([alt_suffix_parts[-1]] if alt_suffix_parts else [])
                part_str = ", ".join(f"'{p}'" for p in all_parts)
                alt_part_str = ", ".join(f"'{p}'" for p in all_parts_alt)
            else:
                part_str = f"'{rpath.strip('/')}'"
                alt_part_str = part_str
            lines.append(f"  it('route {safe_path} has a page component', () => {{{comment}")
            lines.append(f"    const pagePath = path.join(process.cwd(), {part_str});")
            lines.append(f"    const altPath = path.join(process.cwd(), {alt_part_str});")
            lines.append(f"    expect(existsSync(pagePath) || existsSync(altPath)).toBe(true);")
            lines.append("  });")
            lines.append("")
        lines.append("});")
        lines.append("")

    content = "\n".join(lines)
    return {"test_wiring_api_completeness.test.ts": content}

def _generate_wiring_python_stubs(
    frontend_routes: List[dict], api_routes: List[dict],
    convention: dict = None,
) -> Dict[str, str]:
    """Generate Python/unittest wiring test stubs.

    F-0: Uses detected route convention for framework-correct paths.

    Produces test stubs that verify:
      - Each API endpoint has a route handler
      - Each frontend route has a page/view
    """
    if convention is None:
        convention = {"api_route_pattern": "src/app/{segments}/route.{ext}",
                      "page_pattern": "src/app/{segments}/page.{ext}",
                      "api_ext": "ts", "page_ext": "tsx",
                      "alt_api_ext": "js", "alt_page_ext": "jsx"}

    lines = [
        '"""',
        "Auto-generated wiring test stubs for API route completeness.",
        "",
        "Verifies that every API endpoint in the navigation map has a",
        "corresponding route handler, and every frontend route has a page.",
        '"""',
        "",
        "import os",
        "import unittest",
        "",
        "",
    ]

    # API Route Completeness
    if api_routes:
        lines.append("class TestApiRouteCompleteness(unittest.TestCase):")
        lines.append('    """Verify every API endpoint has a route handler."""')
        lines.append("")
        for route in api_routes:
            rpath = route["path"]
            func_name = rpath.replace("/", "_").replace("-", "_").strip("_")
            safe_path = rpath.replace("'", "\\'")
            segments = [s for s in rpath.strip("/").split("/") if s]
            # Resolve path from convention
            api_pattern = convention["api_route_pattern"]
            ext = convention["api_ext"]
            alt_ext = convention["alt_api_ext"]
            if "{segments}" in api_pattern:
                prefix, suffix = api_pattern.split("{segments}", 1)
                prefix_parts = [p for p in prefix.strip("/").split("/") if p]
                suffix_file = suffix.replace("{ext}", ext).strip("/")
                alt_suffix_file = suffix.replace("{ext}", alt_ext).strip("/")
                all_parts = prefix_parts + segments + ([suffix_file] if suffix_file else [])
                alt_parts = prefix_parts + segments + ([alt_suffix_file] if alt_suffix_file else [])
                part_str = ", ".join(f"'{p}'" for p in all_parts)
                alt_part_str = ", ".join(f"'{p}'" for p in alt_parts)
            else:
                part_str = f"'{rpath.strip('/')}'"
                alt_part_str = part_str
            lines.append(f"    def test_handler_{func_name}(self):")
            lines.append(f'        """Route handler must exist for {safe_path}."""')
            lines.append(f"        route_path = os.path.join(os.getcwd(), {part_str})")
            lines.append(f"        alt_path = os.path.join(os.getcwd(), {alt_part_str})")
            lines.append(f"        self.assertTrue(")
            lines.append(f"            os.path.exists(route_path) or os.path.exists(alt_path),")
            lines.append(f"            f'Missing route handler: {{route_path}}'")
            lines.append(f"        )")
            lines.append("")

    # Frontend Route Accessibility
    if frontend_routes:
        lines.append("class TestFrontendRouteAccessibility(unittest.TestCase):")
        lines.append('    """Verify every frontend route has a page/view."""')
        lines.append("")
        for route in frontend_routes:
            rpath = route["path"]
            func_name = rpath.replace("/", "_").replace("-", "_").strip("_")
            if not func_name:
                func_name = "root"
            safe_path = rpath.replace("'", "\\'")
            segments = [s for s in rpath.strip("/").split("/") if s]
            # Resolve page path from convention
            page_pattern = convention["page_pattern"]
            ext = convention["page_ext"]
            alt_ext = convention["alt_page_ext"]
            if "{segments}" in page_pattern:
                prefix, suffix = page_pattern.split("{segments}", 1)
                prefix_parts = [p for p in prefix.strip("/").split("/") if p]
                suffix_file = suffix.replace("{ext}", ext).strip("/")
                alt_suffix_file = suffix.replace("{ext}", alt_ext).strip("/")
                all_parts = prefix_parts + segments + ([suffix_file] if suffix_file else [])
                alt_parts = prefix_parts + segments + ([alt_suffix_file] if alt_suffix_file else [])
                part_str = ", ".join(f"'{p}'" for p in all_parts)
                alt_part_str = ", ".join(f"'{p}'" for p in alt_parts)
            else:
                part_str = f"'{rpath.strip('/')}'"
                alt_part_str = part_str
            lines.append(f"    def test_page_{func_name}(self):")
            lines.append(f'        """Page component must exist for {safe_path}."""')
            lines.append(f"        page_path = os.path.join(os.getcwd(), {part_str})")
            lines.append(f"        alt_path = os.path.join(os.getcwd(), {alt_part_str})")
            lines.append(f"        self.assertTrue(")
            lines.append(f"            os.path.exists(page_path) or os.path.exists(alt_path),")
            lines.append(f"            f'Missing page component: {{page_path}}'")
            lines.append(f"        )")
            lines.append("")

    lines.append("")
    lines.append("if __name__ == '__main__':")
    lines.append("    unittest.main()")
    lines.append("")

    content = "\n".join(lines)
    return {"test_wiring_api_completeness.py": content}


def _generate_sdk_import_stubs(
    requirements: list,
    framework: str = "vitest",
) -> Dict[str, str]:
    """Generate SDK import verification test stubs.

    Scans requirements for integration-category items, calls _extract_sdk_name
    to get the npm package name, and generates executable test assertions that
    verify the SDK is actually imported somewhere in the source code.

    Uses globSync + readFileSync to scan src/**/*.{ts,tsx,js,jsx} for import
    statements matching the SDK package name.

    Args:
        requirements: List of requirement dicts from the ledger.
        framework: Test framework for import line (vitest/jest/mocha).

    Returns:
        Dict mapping filename -> file content. Empty dict if no SDK
        integrations found.
    """
    sdk_reqs: List[tuple] = []  # (sdk_package, req_text)
    seen_sdks: set = set()

    for req in requirements:
        category = req.get("category", "").lower()
        if "integration" not in category:
            continue
        req_text = f"{req.get('title', '')} {req.get('text', '')} {req.get('description', '')}"
        sdk_name = _extract_sdk_name(req_text)
        if sdk_name and sdk_name not in seen_sdks:
            sdk_reqs.append((sdk_name, req_text.strip()))
            seen_sdks.add(sdk_name)

    if not sdk_reqs:
        return {}

    test_import = _get_test_import_line(framework)
    lines = [
        test_import,
        "import { readFileSync } from 'fs';",
        "import { globSync } from 'glob';",
        "",
        "describe('SDK Integration Import Verification', () => {",
    ]

    for sdk_package, req_text in sdk_reqs:
        safe_sdk = sdk_package.replace("'", "\\'")
        lines.append(f"  it('{safe_sdk} SDK is imported in source code', () => {{")
        lines.append(f"    const srcFiles = globSync('src/**/*.{{ts,tsx,js,jsx}}');")
        lines.append(f"    const hasImport = srcFiles.some(f => {{")
        lines.append(f"      const content = readFileSync(f, 'utf-8');")
        lines.append(f"      return content.includes(\"from '{safe_sdk}'\")"
                     f" || content.includes('from \"{safe_sdk}\"');")
        lines.append(f"    }});")
        lines.append(f"    expect(hasImport).toBe(true);")
        lines.append(f"  }});")
        lines.append("")

    lines.append("});")
    lines.append("")

    content = "\n".join(lines)
    return {"test_sdk_import_verification.test.ts": content}


# ── F-0: Lifecycle wiring patterns ───────────────────────────────────────
# Keywords that indicate a requirement involves lifecycle wiring (function
# defined → must be called from entry point).
_LIFECYCLE_PATTERNS = {
    "cron": {
        "keywords": ["cron", "scheduled", "schedule", "scheduler", "daily", "weekly", "hourly", "interval"],
        "entry_points": [
            # Next.js / Node.js
            "instrumentation.ts", "instrumentation.js", "server.ts", "server.js",
            "_app.tsx", "_app.jsx", "index.ts", "index.js",
            # Python frameworks
            "main.ts", "main.py", "app.py", "wsgi.py", "asgi.py", "celery.py",
        ],
        "description": "cron/scheduler",
    },
    "middleware": {
        "keywords": ["middleware", "interceptor", "guard", "rate limit", "rate-limit", "cors"],
        "entry_points": [
            # Next.js
            "middleware.ts", "middleware.js",
            # Express / Node.js
            "server.ts", "server.js", "_app.tsx", "_app.jsx",
            # Django / Flask
            "settings.py", "app.py", "main.py",
        ],
        "description": "middleware",
    },
    "event_listener": {
        "keywords": ["event listener", "webhook", "websocket", "on_event", "event handler", "pub/sub", "pubsub"],
        "entry_points": [
            # Node.js
            "server.ts", "server.js", "instrumentation.ts", "index.ts",
            # Python
            "main.ts", "main.py", "app.py", "events.py",
        ],
        "description": "event listener/webhook",
    },
}


def _generate_lifecycle_wiring_stubs(
    requirements: list,
    framework: str = "vitest",
) -> Dict[str, str]:
    """Generate TDD stubs for lifecycle wiring verification.

    F-0 (RCA-462): When a requirement mentions cron, middleware, event
    listeners, or other lifecycle hooks, this generates executable tests
    that verify the function is not just defined but actually CALLED
    from an entry point. Prevents the 'defined but never wired' class
    of bugs where setupCronJobs() exists but is never invoked.

    Args:
        requirements: List of requirement dicts from the skeleton.
        framework: Test framework (vitest/jest/mocha).

    Returns:
        Dict mapping filename -> content. Empty if no lifecycle reqs found.
    """
    if not requirements:
        return {}

    # Detect lifecycle requirements
    lifecycle_reqs: List[dict] = []
    for req in requirements:
        req_text = (req.get("text", "") + " " + req.get("title", "") + " " + req.get("description", "")).lower()
        for pattern_type, pattern_config in _LIFECYCLE_PATTERNS.items():
            if any(kw in req_text for kw in pattern_config["keywords"]):
                lifecycle_reqs.append({
                    "req": req,
                    "type": pattern_type,
                    "config": pattern_config,
                })
                break  # One match per requirement

    if not lifecycle_reqs:
        return {}

    test_import = _get_test_import_line(framework)
    lines = [
        "// F-0 (RCA-462): Lifecycle Wiring Verification Tests",
        "// Verifies that lifecycle functions (cron, middleware, event listeners)",
        "// are not just defined but actually CALLED from entry points.",
        "// A function that exists but is never wired is dead code.",
        "",
        test_import,
        "import { readFileSync, existsSync } from 'fs';",
        "import { globSync } from 'glob';",
        "",
    ]

    lines.append("describe('Lifecycle Wiring Verification', () => {")

    for item in lifecycle_reqs:
        req = item["req"]
        req_id = req.get("req_id", "UNKNOWN")
        req_text = req.get("text", "")
        pattern_type = item["type"]
        config = item["config"]
        entry_points = config["entry_points"]
        desc = config["description"]

        # Sanitize for test name
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', req_id).strip('_').lower()

        # Test 1: Lifecycle function is defined somewhere
        lines.append(f"  // {req_id}: {req_text}")
        lines.append(f"  it('{req_id} — {desc} function is defined in source code', () => {{")
        lines.append(f"    const srcFiles = globSync('src/**/*.{{ts,tsx,js,jsx}}');")
        lines.append(f"    const keywords = {json.dumps(config['keywords'][:3])};")
        lines.append(f"    const hasDefinition = srcFiles.some((f: string) => {{")
        lines.append(f"      const content = readFileSync(f, 'utf-8').toLowerCase();")
        lines.append(f"      return keywords.some((kw: string) => content.includes(kw));")
        lines.append(f"    }});")
        lines.append(f"    expect(hasDefinition).toBe(true);")
        lines.append(f"  }});")
        lines.append("")

        # Test 2: Lifecycle function is called from an entry point
        entry_point_checks = " || ".join(
            f"existsSync('src/{ep}')" for ep in entry_points[:4]
        )
        entry_point_names = json.dumps(entry_points[:4])
        lines.append(f"  it('{req_id} — {desc} is wired from an entry point', () => {{")
        lines.append(f"    // Entry points for {desc}: {', '.join(entry_points[:4])}")
        lines.append(f"    const entryPointPaths = {entry_point_names};")
        lines.append(f"    const keywords = {json.dumps(config['keywords'][:3])};")
        lines.append(f"    let wired = false;")
        lines.append(f"    for (const ep of entryPointPaths) {{")
        lines.append(f"      const fullPath = `src/${{ep}}`;")
        lines.append(f"      if (existsSync(fullPath)) {{")
        lines.append(f"        const content = readFileSync(fullPath, 'utf-8').toLowerCase();")
        lines.append(f"        if (keywords.some((kw: string) => content.includes(kw))) {{")
        lines.append(f"          wired = true;")
        lines.append(f"          break;")
        lines.append(f"        }}")
        lines.append(f"      }}")
        lines.append(f"    }}")
        lines.append(f"    expect(wired).toBe(true);")
        lines.append(f"  }});")
        lines.append("")

    lines.append("});")
    lines.append("")

    content = "\n".join(lines)
    return {"test_lifecycle_wiring.test.ts": content}


def _generate_service_wiring_stubs(
    requirements: list,
    project_dir: str = "",
) -> List[Dict[str, str]]:
    """Generate TDD test stubs for API → Service wiring.

    F-6 (RCA-461): Island Systems exist because no test verifies
    that API routes actually import and call their service modules.
    This generates structural import/call tests for each route→service pair.

    Analyzes requirements for API route references (e.g., titles or
    descriptions containing '/api/' patterns) and produces test stubs that
    verify:
      1. API routes import their service modules
      2. Service modules exist and are importable
      3. Route handler functions call the corresponding service functions

    Args:
        requirements: List of requirement dicts from the ledger. Each dict
            may have 'id', 'title', 'description', and other fields.
        project_dir: Project directory path (for reading architecture).

    Returns:
        List of dicts with 'test_name', 'test_code', and 'description' keys.
        Empty list if no API-related requirements found.
    """
    if not requirements:
        return []

    # Pattern to detect API routes in requirement titles/descriptions
    api_pattern = re.compile(
        r'(GET|POST|PUT|DELETE|PATCH)\s+(/api/[^\s,)]+)',
        re.IGNORECASE,
    )
    # Fallback: match any /api/ path reference
    api_path_pattern = re.compile(r'(/api/[a-zA-Z0-9_/\-]+)')

    stubs: List[Dict[str, str]] = []
    seen_routes: set = set()

    for req in requirements:
        title = req.get("title", "")
        description = req.get("description", "")
        req_id = req.get("id", req.get("req_id", "UNKNOWN"))
        combined_text = f"{title} {description}"

        # Try to extract API route from title/description
        routes_found: List[tuple] = []

        # First try explicit method + path
        for match in api_pattern.finditer(combined_text):
            method = match.group(1).upper()
            path = match.group(2)
            routes_found.append((method, path))

        # Fallback: just path (no method prefix)
        if not routes_found:
            for match in api_path_pattern.finditer(combined_text):
                path = match.group(1)
                routes_found.append(("ANY", path))

        for method, path in routes_found:
            if path in seen_routes:
                continue
            seen_routes.add(path)

            # Derive service name from route path:
            # /api/reviews → reviews, /api/users/profile → users
            path_parts = [p for p in path.split("/") if p and p != "api"]
            if not path_parts:
                continue
            service_name = path_parts[0]  # Primary resource name

            # Sanitize for test function name
            safe_name = re.sub(r'[^a-zA-Z0-9]', '_', service_name).strip('_').lower()
            safe_path = path.replace("'", "\\'")

            # Stub 1: Route handler imports service module
            import_test_name = f"test_{safe_name}_route_imports_service"
            import_test_code = (
                f"# F-6 (RCA-461): Verify {safe_path} route imports {service_name} service\n"
                f"# The route handler file for {safe_path} must import from the\n"
                f"# {service_name} service module. Without this import, the route\n"
                f"# is an island system — it exists but doesn't connect to business logic.\n"
                f"#\n"
                f"# Implementation: Read the route handler source file and assert\n"
                f"# it contains an import statement referencing the {service_name} service.\n"
                f"# Example assertion:\n"
                f"#   assert 'import' in route_source and '{service_name}' in route_source\n"
                f"raise NotImplementedError(\n"
                f"    'TODO: Verify {safe_path} route handler imports {service_name} service'\n"
                f")"
            )
            stubs.append({
                "test_name": import_test_name,
                "test_code": import_test_code,
                "description": (
                    f"F-6 service wiring: Verify {safe_path} route handler imports "
                    f"the {service_name} service module ({req_id})"
                ),
            })

            # Stub 2: Service module exists and is importable
            service_test_name = f"test_{safe_name}_service_module_exists"
            service_test_code = (
                f"# F-6 (RCA-461): Verify {service_name} service module exists\n"
                f"# The service layer for {safe_path} must exist as an importable module.\n"
                f"# This prevents island systems where routes reference nonexistent services.\n"
                f"#\n"
                f"# Implementation: Check that a service file exists for {service_name}.\n"
                f"# Example assertion:\n"
                f"#   import glob\n"
                f"#   service_files = glob.glob('**/services/{service_name}*', recursive=True)\n"
                f"#   assert len(service_files) > 0\n"
                f"raise NotImplementedError(\n"
                f"    'TODO: Verify {service_name} service module exists and is importable'\n"
                f")"
            )
            stubs.append({
                "test_name": service_test_name,
                "test_code": service_test_code,
                "description": (
                    f"F-6 service wiring: Verify {service_name} service module "
                    f"exists and is importable ({req_id})"
                ),
            })

            # Stub 3: Route handler calls service function
            call_test_name = f"test_{safe_name}_route_calls_service"
            call_test_code = (
                f"# F-6 (RCA-461): Verify {safe_path} handler calls {service_name} service\n"
                f"# The route handler for {method} {safe_path} must call a function\n"
                f"# from the {service_name} service. This verifies the route is not\n"
                f"# just importing the service but actually invoking its logic.\n"
                f"#\n"
                f"# Implementation: Read route handler source, find calls to service functions.\n"
                f"# Example assertion:\n"
                f"#   assert '{service_name}' in route_source  # service function call\n"
                f"raise NotImplementedError(\n"
                f"    'TODO: Verify {safe_path} handler calls {service_name} service function'\n"
                f")"
            )
            stubs.append({
                "test_name": call_test_name,
                "test_code": call_test_code,
                "description": (
                    f"F-6 service wiring: Verify {safe_path} handler calls "
                    f"the {service_name} service function ({req_id})"
                ),
            })

    logger.info(
        "[TDD STUBS] F-6: Generated %d service wiring test stubs from %d requirements",
        len(stubs), len(requirements),
    )
    return stubs


def _render_wire6_stubs(int_stubs: list) -> str:
    """Render Wire 6 integration test stubs with deterministic assertions.

    Fix D: Instead of generating `throw new Error('TODO: ...')` placeholders,
    this now generates real deterministic assertions:
      - import_exists → readFileSync(source) + expect().toMatch(/import.*target/)
      - fetch_exists  → readFileSync(source) + expect().toMatch(/fetch.*api/)
      - unknown types → throw new Error (fallback)

    Args:
        int_stubs: List of stub dicts from generate_integration_test_stubs().

    Returns:
        Rendered test file content as a string.
    """
    int_lines = [
        "import { readFileSync } from 'fs';",
        "import path from 'path';",
        "",
        "// Auto-generated integration test stubs from dependency-graph.json",
        "// Wire 6: Each stub verifies a module import or API fetch binding.",
        "",
    ]
    for stub in int_stubs:
        test_name = stub.get("test_name", "test_unknown")
        assertion_type = stub.get("assertion_type", "unknown")
        source_file = stub.get("source_file", "")
        target = stub.get("target", "")
        description = stub.get("description", "")
        req_id = stub.get("req_id", "")

        int_lines.append(f"// {req_id}: {description}")

        if assertion_type == "import_exists":
            # Deterministic: read source file, check it imports the target
            target_basename = target.rsplit("/", 1)[-1].replace(".ts", "").replace(".tsx", "").replace(".js", "").replace(".jsx", "")
            int_lines.append(f"test('{test_name}', () => {{")
            int_lines.append(f"  const src = readFileSync(path.resolve('{source_file}'), 'utf-8');")
            int_lines.append(f"  expect(src).toMatch(/import.*{target_basename}/);")
            int_lines.append("});")

        elif assertion_type == "fetch_exists":
            # Deterministic: read source file, check it fetches the API path
            # Escape slashes in API path for regex
            api_escaped = target.replace("/", "\\/")
            int_lines.append(f"test('{test_name}', () => {{")
            int_lines.append(f"  const src = readFileSync(path.resolve('{source_file}'), 'utf-8');")
            int_lines.append(f"  expect(src).toMatch(/fetch.*{api_escaped}/);")
            int_lines.append("});")

        else:
            # Unknown assertion type — keep throw Error as fallback
            int_lines.append(f"test('{test_name}', () => {{")
            int_lines.append(f"  throw new Error('Unknown assertion type: {assertion_type} for {source_file} -> {target}');")
            int_lines.append("});")

        int_lines.append("")

    return "\n".join(int_lines)


def generate_tdd_tests(project_dir: str, phase_req_ids: list = None) -> Dict[str, str]:
    """Generate TDD test module stubs from the test skeleton.

    F-5: Language-aware — detects project language and generates stubs
    in the appropriate syntax:
      - TypeScript projects (package.json): vitest describe/it/expect
      - Python projects (pyproject.toml/setup.py/default): unittest

    Reads docs/test-skeleton.json and produces test file stubs grouped by
    test_type (unit, integration, e2e, literal, config). Each stub contains:
      - Import boilerplate
      - A test class/describe per test_type
      - A test function/it per requirement with REQ-ID
      - Expected literal assertions when available

    Stubs are written to docs/tdd/ and ALSO copied to the project's
    test runner directory (F-6: src/__tests__/ or tests/) so that
    `npm test` / `pytest` auto-discovers them.

    Args:
        project_dir: Path to the project directory.
        phase_req_ids: Optional list of requirement IDs. When provided,
            only requirements whose req_id is in this list will get stubs.
            This enables per-phase stub generation at delegation time
            (RCA: TDD spiral fix — code agent only sees its own phase's tests).

    Universal: works for any project with a test-skeleton.json.
    """
    skeleton_path = os.path.join(project_dir, "docs", "test-skeleton.json")
    if not os.path.isfile(skeleton_path):
        logger.warning("[TDD STUBS] No test-skeleton.json found, skipping")
        return {}

    with open(skeleton_path, "r") as f:
        skeleton = json.load(f)

    # SS-4 (ITR-355): Idempotency check — skip regeneration if skeleton
    # hasn't changed since last generation. Prevents redundant 6x regeneration
    # when BDD gate fires multiple times with unchanged content.
    skeleton_str = json.dumps(skeleton, sort_keys=True)
    skeleton_hash = hashlib.md5(skeleton_str.encode()).hexdigest()
    stubs_dir = os.path.join(project_dir, "docs", "tdd")
    hash_path = os.path.join(stubs_dir, ".tdd_hash")
    if os.path.isfile(hash_path):
        try:
            with open(hash_path, "r") as hf:
                existing_hash = hf.read().strip()
            if existing_hash == skeleton_hash:
                # Stubs are up-to-date — read existing and return
                existing_stubs = {}
                for fname in os.listdir(stubs_dir):
                    if fname.startswith("."):
                        continue
                    fpath = os.path.join(stubs_dir, fname)
                    if os.path.isfile(fpath):
                        with open(fpath, "r") as sf:
                            existing_stubs[fname] = sf.read()
                logger.info(
                    f"[TDD STUBS] Skipping regeneration — skeleton unchanged "
                    f"(hash={skeleton_hash[:8]}). {len(existing_stubs)} existing stubs."
                )
                return existing_stubs
        except (IOError, OSError):
            pass  # Hash file unreadable — regenerate

    requirements = skeleton.get("requirements", [])

    # Filter out delivery standards and scaffold REQs
    reqs = [
        r for r in requirements
        if not r.get("req_id", "").startswith("REQ-DELIVERY")
        and not r.get("req_id", "").startswith("REQ-SCAFFOLD")
    ]

    # Phase-scoped filtering: when phase_req_ids is provided, only include
    # requirements whose req_id is in the list. This enables per-phase TDD
    # stub generation at delegation time (RCA: TDD spiral fix).
    if phase_req_ids is not None:
        phase_set = set(phase_req_ids)
        reqs = [r for r in reqs if r.get("req_id", "") in phase_set]

    if not reqs:
        return {}

    # Group by test_type
    groups: Dict[str, list] = {}
    for req in reqs:
        test_type = req.get("test_type", "unit")
        groups.setdefault(test_type, []).append(req)

    # F-5: Detect project language and dispatch to appropriate generator
    language = detect_project_language(project_dir)

    # FIX-3: Detect test framework for TypeScript projects
    test_framework = detect_test_framework(project_dir)

    # FIX-D (ITR-34): Write test-config.json so the RED baseline validator
    # knows how to run tests. The agent knows the framework — no hardcoding.
    _write_test_config(project_dir, language)

    # RCA-470 Phase 3.9: Generate vitest.config.ts alongside test stubs.
    # Root cause: Code agent spent 15/20 iterations discovering Vitest config
    # by trial-and-error (path aliases, jsdom, globals). This generates a
    # ready-to-use config file so tests can run immediately.
    vitest_config_content = _generate_vitest_config(language)
    if vitest_config_content:
        vitest_config_path = os.path.join(project_dir, "vitest.config.ts")
        if not os.path.isfile(vitest_config_path):
            try:
                with open(vitest_config_path, "w", encoding="utf-8") as vc:
                    vc.write(vitest_config_content)
                logger.info(
                    "[TDD STUBS] RCA-470: Generated vitest.config.ts "
                    "(jsdom + tsconfigPaths + globals)"
                )
            except (IOError, OSError) as ve:
                logger.warning(f"[TDD STUBS] Could not write vitest.config.ts: {ve}")
        else:
            logger.debug("[TDD STUBS] vitest.config.ts already exists — skipping")

    # F-0: Load BDD scenarios for behavioral assertion generation
    bdd_map = _load_bdd_scenarios(project_dir)
    if bdd_map:
        logger.info(
            f"[TDD STUBS] F-0: Loaded BDD scenarios for {len(bdd_map)} requirements"
        )

    if language == "typescript":
        result = _generate_typescript_stubs(groups, reqs, project_dir, framework=test_framework, bdd_map=bdd_map)
    elif language == "python":
        result = _generate_python_stubs(groups, reqs, project_dir, bdd_map=bdd_map)
    else:
        # Unknown language — generate universal pseudocode stubs
        # The LLM will convert these to the correct language
        result = _generate_universal_stubs(groups, reqs, project_dir, bdd_map=bdd_map)

    # F-6: Copy stubs to the test runner directory for auto-discovery.
    # This runs AFTER the language-specific generator writes to docs/tdd/.
    test_dir_paths = _write_stubs_to_test_dir(result, project_dir, language)
    if test_dir_paths:
        logger.info(
            f"[TDD STUBS] F-6: {len(test_dir_paths)} stubs wired to test runner dir"
        )

    # SS-4 (ITR-355): Write content hash after successful generation
    # so subsequent calls with unchanged skeleton skip regeneration.
    try:
        os.makedirs(stubs_dir, exist_ok=True)
        with open(hash_path, "w") as hf:
            hf.write(skeleton_hash)
    except (IOError, OSError) as e:
        logger.warning(f"[TDD STUBS] Could not write hash file: {e}")

    # Wiring test stubs: generate API route completeness tests from navigation map
    wiring_stubs = generate_wiring_test_stubs(project_dir)
    if wiring_stubs:
        result.update(wiring_stubs)
        logger.info(
            f"[TDD STUBS] Merged {len(wiring_stubs)} wiring test stubs into result"
        )

    # SDK Import Verification stubs: verify integration SDKs are imported
    sdk_stubs = _generate_sdk_import_stubs(reqs, framework=test_framework)
    if sdk_stubs:
        result.update(sdk_stubs)
        # Write to docs/tdd/
        tdd_dir = os.path.join(project_dir, "docs", "tdd")
        os.makedirs(tdd_dir, exist_ok=True)
        for fname, content in sdk_stubs.items():
            try:
                with open(os.path.join(tdd_dir, fname), "w") as sf:
                    sf.write(content)
            except (IOError, OSError) as e:
                logger.warning("[TDD STUBS] Could not write SDK import stubs: %s", e)
        logger.info(
            f"[TDD STUBS] Generated {len(sdk_stubs)} SDK import verification stubs"
        )

    # F-0 (RCA-462): Lifecycle wiring stubs — verify cron/middleware/events
    # are called from entry points, not just defined
    lifecycle_stubs = _generate_lifecycle_wiring_stubs(reqs, framework=test_framework)
    if lifecycle_stubs:
        result.update(lifecycle_stubs)
        # Write to docs/tdd/
        tdd_dir = os.path.join(project_dir, "docs", "tdd")
        os.makedirs(tdd_dir, exist_ok=True)
        for fname, content in lifecycle_stubs.items():
            try:
                with open(os.path.join(tdd_dir, fname), "w") as sf:
                    sf.write(content)
            except (IOError, OSError) as e:
                logger.warning("[TDD STUBS] Could not write lifecycle wiring stubs: %s", e)
        logger.info(
            f"[TDD STUBS] Generated {len(lifecycle_stubs)} lifecycle wiring stubs"
        )

    # F-6 (RCA-461): Service wiring stubs — verify API→Service import/call chains
    svc_wiring_stubs = _generate_service_wiring_stubs(reqs, project_dir)
    if svc_wiring_stubs:
        # Render service wiring stubs into a test file
        svc_lines = [
            "# Auto-generated service wiring test stubs (F-6 / RCA-461)",
            "# Each test verifies that an API route imports and calls its service module.",
            "# Replace TODO assertions with real implementation checks.",
            "",
            "import unittest",
            "",
            "",
            "class TestServiceWiring(unittest.TestCase):",
            '    """F-6: Verify API routes are wired to their service modules."""',
            "",
        ]
        for stub in svc_wiring_stubs:
            test_name = stub.get("test_name", "test_unknown")
            test_code = stub.get("test_code", "pass")
            description = stub.get("description", "")
            svc_lines.append(f"    def {test_name}(self):")
            svc_lines.append(f'        """{description}"""')
            for code_line in test_code.split("\n"):
                svc_lines.append(f"        {code_line}")
            svc_lines.append("")

        svc_lines.append("")
        svc_lines.append("if __name__ == '__main__':")
        svc_lines.append("    unittest.main()")
        svc_lines.append("")

        svc_stub_content = "\n".join(svc_lines)
        svc_stub_filename = _to_reference_name("test_service_wiring.py")
        result[svc_stub_filename] = svc_stub_content

        # Also write to docs/tdd/
        tdd_dir = os.path.join(project_dir, "docs", "tdd")
        os.makedirs(tdd_dir, exist_ok=True)
        svc_stub_path = os.path.join(tdd_dir, svc_stub_filename)
        try:
            with open(svc_stub_path, "w") as sf:
                sf.write(svc_stub_content)
        except (IOError, OSError) as e:
            logger.warning("[TDD STUBS] F-6: Could not write service wiring stubs: %s", e)

        logger.info(
            "[TDD STUBS] F-6: Generated %d service wiring test stubs from requirements",
            len(svc_wiring_stubs),
        )

    # Wire 6: Integration test stubs from dependency graph
    # When dependency-graph.json exists, generate import_exists and
    # fetch_exists assertion stubs via generate_integration_test_stubs().
    dep_graph_path = os.path.join(project_dir, "docs", "dependency-graph.json")
    if os.path.isfile(dep_graph_path):
        try:
            with open(dep_graph_path, "r") as dgf:
                dep_graph = json.load(dgf)

            from python.helpers.budget_cost_model import generate_integration_test_stubs
            int_stubs = generate_integration_test_stubs(dep_graph)

            if int_stubs:
                # Render integration stubs via _render_wire6_stubs (Fix D)
                stub_content = _render_wire6_stubs(int_stubs)

                stub_filename = _to_reference_name("integration_dep_graph.test.ts")
                result[stub_filename] = stub_content

                # Also write to docs/tdd/
                tdd_dir = os.path.join(project_dir, "docs", "tdd")
                os.makedirs(tdd_dir, exist_ok=True)
                with open(os.path.join(tdd_dir, stub_filename), "w") as sf:
                    sf.write(stub_content)

                logger.info(
                    f"[TDD STUBS] Wire-6: Generated {len(int_stubs)} integration "
                    f"test stubs from dependency-graph.json"
                )
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.warning(f"[TDD STUBS] Wire-6: Could not process dependency-graph.json: {e}")

    return result
