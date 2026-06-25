"""
Non-web project quality checks — CLI tools, API servers, libraries.

These checks complement the web_only=True checks by providing domain-
specific validation for non-web projects. Without these, non-web projects
only received universal checks (TDD, contract assertions, etc.) with no
CLI/API-specific validation.

Each check uses _is_web_project() internally to skip for web projects,
rather than using a hypothetical non_web_only flag. This keeps the
registry infrastructure unchanged (no new flag needed).

Check catalog:
  - CLI entry point (blocking, gate=done): main.py, cli.py, app.py, etc.
  - Non-web test execution (blocking, gate=tdd): tests/, test_*.py, spec/
  - Package manifest (blocking, gate=done): pyproject.toml, setup.py, etc.
  - API route handlers (advisory): Flask/FastAPI/Express route definitions
  - Non-web documentation (advisory): README.md/.rst/.txt
"""

import os
import re
import logging

from python.helpers.orchestrator_gate_integration_checks import (
    register_check,
    register_advisory,
    CheckContext,
    _is_web_project,
)


logger = logging.getLogger("agix.checks.non_web")


# ─── Entry Point File Names ────────────────────────────────────────────

# Root-level entry point file names (any of these = valid CLI/API entry)
_ROOT_ENTRY_POINTS = [
    "main.py", "cli.py", "app.py", "server.py", "run.py",
    "manage.py",           # Django
    "__main__.py",         # Python package entry
    "index.js", "index.ts", "index.mjs",
    "server.js", "server.ts",
    "app.js", "app.ts",
    "main.go",             # Go
    "main.rs",             # Rust (src/main.rs)
    "Makefile",            # Build-system entry
]

# Nested locations to check (relative to project root)
_NESTED_ENTRY_DIRS = ["src", "cmd", "bin", "lib"]

# Directories that count as entry points by existing (Go cmd/ convention)
_ENTRY_POINT_DIRS = ["cmd", "bin"]

# ─── Package Manifest File Names ──────────────────────────────────────

_PACKAGE_MANIFESTS = [
    "pyproject.toml", "setup.py", "setup.cfg",   # Python
    "requirements.txt", "Pipfile",                # Python deps
    "Cargo.toml",                                 # Rust
    "go.mod",                                     # Go
    "pom.xml", "build.gradle", "build.gradle.kts",  # Java
    "Makefile", "CMakeLists.txt",                 # C/C++/Make
    "Gemfile",                                    # Ruby
    "mix.exs",                                    # Elixir
    "Package.swift",                              # Swift
]

# ─── Test Directory / File Indicators ─────────────────────────────────

_TEST_DIRS = ["tests", "test", "spec", "__tests__"]
_TEST_FILE_PATTERNS = [
    re.compile(r"^test_.*\.py$"),      # Python: test_*.py
    re.compile(r"^.*_test\.py$"),       # Python: *_test.py
    re.compile(r"^.*_test\.go$"),       # Go: *_test.go
    re.compile(r"^.*\.spec\.\w+$"),    # JS/Ruby: *.spec.js, *.spec.rb
    re.compile(r"^.*\.test\.\w+$"),    # JS: *.test.js, *.test.ts
]

# ─── API Framework Indicators ────────────────────────────────────────

_API_FRAMEWORK_IMPORTS = [
    re.compile(r"from\s+flask\s+import", re.IGNORECASE),
    re.compile(r"from\s+fastapi\s+import", re.IGNORECASE),
    re.compile(r"import\s+express", re.IGNORECASE),
    re.compile(r"from\s+django\b", re.IGNORECASE),
    re.compile(r"from\s+starlette\b", re.IGNORECASE),
    re.compile(r"from\s+sanic\b", re.IGNORECASE),
    re.compile(r"require\(['\"]express['\"]\)", re.IGNORECASE),
    re.compile(r"import.*\bFastAPI\b"),
    re.compile(r"import.*\bFlask\b"),
]

_API_ROUTE_PATTERNS = [
    re.compile(r"@app\.(route|get|post|put|delete|patch)\s*\("),     # Flask/FastAPI
    re.compile(r"@router\.(get|post|put|delete|patch)\s*\("),        # FastAPI Router
    re.compile(r"app\.(get|post|put|delete|patch|use)\s*\("),        # Express
    re.compile(r"path\s*\(\s*['\"]"),                                 # Django urls
    re.compile(r"urlpatterns\s*="),                                   # Django urls
]


# ═══════════════════════════════════════════════════════════════════════
# 1. CLI Entry Point Check (Blocking, gate=done)
# ═══════════════════════════════════════════════════════════════════════

@register_check(3.01, "CLI entry point", critical=True, gate="done")
def _check_cli_entry_point(ctx: CheckContext) -> dict:
    """Verify CLI/API project has a main entry point file.

    Skips for web projects (they have their own entry via package.json scripts).
    For non-web projects, checks for common entry point files (main.py, cli.py,
    app.py, index.js, etc.) at root level and in standard nested locations
    (src/, cmd/, bin/).

    Returns None if entry point found or web project, block message otherwise.
    """
    if _is_web_project(ctx.project_dir):
        return None

    if not ctx.project_dir or not os.path.isdir(ctx.project_dir):
        return None

    # Check root-level entry points
    for fname in _ROOT_ENTRY_POINTS:
        if os.path.isfile(os.path.join(ctx.project_dir, fname)):
            return None

    # Check nested entry points (src/main.py, cmd/main.go, etc.)
    for subdir in _NESTED_ENTRY_DIRS:
        nested = os.path.join(ctx.project_dir, subdir)
        if not os.path.isdir(nested):
            continue
        for fname in _ROOT_ENTRY_POINTS:
            if os.path.isfile(os.path.join(nested, fname)):
                return None

    # Check entry point directories (cmd/, bin/)
    for dirname in _ENTRY_POINT_DIRS:
        if os.path.isdir(os.path.join(ctx.project_dir, dirname)):
            return None

    return ctx.block(
        "⛔ NO ENTRY POINT: Non-web project has no recognizable main entry "
        "point file. Expected one of: main.py, cli.py, app.py, index.js, "
        "index.ts, server.py, manage.py, main.go, or a cmd/ directory.",
        action=(
            "Create a main entry point file (e.g., main.py for Python, "
            "index.js for Node, main.go for Go) in the project root or src/ directory."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# 2. API Route Handler Advisory
# ═══════════════════════════════════════════════════════════════════════

@register_advisory(3.02, "API route handlers")
def _check_api_route_handlers(ctx: CheckContext):
    """Advisory: verify API projects have route handlers defined.

    Only fires when an API framework is imported (Flask, FastAPI, Express,
    Django) but no route definitions are found. Pure CLI projects (no
    framework import) pass silently — this check is API-specific.

    Skips for web projects (handled by web-specific route checks).
    """
    if _is_web_project(ctx.project_dir):
        return None

    if not ctx.project_dir or not os.path.isdir(ctx.project_dir):
        return None

    # Scan Python/JS source files for API framework imports and routes
    source_exts = {".py", ".js", ".ts", ".mjs"}
    has_api_framework = False
    has_route_definitions = False

    for root, dirs, files in os.walk(ctx.project_dir):
        # Skip common non-source directories
        dirs[:] = [d for d in dirs if d not in {
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            ".next", "dist", "build", ".tox", ".mypy_cache",
        }]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in source_exts:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue

            # Check for API framework imports
            for pattern in _API_FRAMEWORK_IMPORTS:
                if pattern.search(content):
                    has_api_framework = True
                    break

            # Check for route definitions
            for pattern in _API_ROUTE_PATTERNS:
                if pattern.search(content):
                    has_route_definitions = True
                    break

            # Early exit if both found
            if has_api_framework and has_route_definitions:
                return None

    # If API framework found but no routes → advisory warning
    if has_api_framework and not has_route_definitions:
        return (
            "⚠️ API ROUTES MISSING: An API framework (Flask, FastAPI, Express, "
            "or Django) is imported but no route handlers were found. Define "
            "route handlers (e.g., @app.get('/endpoint')) to make the API functional."
        )

    # No API framework → not an API project, pass silently
    return None


# ═══════════════════════════════════════════════════════════════════════
# 3. Non-Web Test Execution Check (Blocking, gate=tdd)
# ═══════════════════════════════════════════════════════════════════════

@register_check(3.03, "Non-web test execution", critical=True, gate="tdd")
def _check_non_web_test_execution(ctx: CheckContext):
    """Verify non-web project has test files.

    Checks for:
    - Test directories: tests/, test/, spec/, __tests__/
    - Test files at root: test_*.py, *_test.py, *_test.go, *.spec.*, *.test.*

    Skips for web projects (they have their own TDD/test checks).
    Does NOT require browser-based tests — only unit/integration tests.
    """
    if _is_web_project(ctx.project_dir):
        return None

    if not ctx.project_dir or not os.path.isdir(ctx.project_dir):
        return None

    # Check for test directories with files in them
    for test_dir_name in _TEST_DIRS:
        test_dir = os.path.join(ctx.project_dir, test_dir_name)
        if os.path.isdir(test_dir):
            # Directory exists — check it has at least one file
            try:
                entries = os.listdir(test_dir)
                if any(not e.startswith(".") for e in entries):
                    return None
            except OSError:
                continue

    # Check for test files at root level
    try:
        root_files = os.listdir(ctx.project_dir)
    except OSError:
        return None

    for fname in root_files:
        for pattern in _TEST_FILE_PATTERNS:
            if pattern.match(fname):
                return None

    return ctx.block(
        "⛔ NO TESTS: Non-web project has no test files or test directories. "
        "Expected tests/ or test/ directory with test files, or test_*.py / "
        "*_test.py files at the project root.",
        action=(
            "Create a tests/ directory with test files. For Python projects, "
            "create tests/test_main.py with pytest-compatible test functions. "
            "For Go, create *_test.go files. For JS/TS, create *.test.ts files."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# 4. README/Documentation Advisory
# ═══════════════════════════════════════════════════════════════════════

@register_advisory(3.04, "Non-web documentation")
def _check_non_web_documentation(ctx: CheckContext):
    """Advisory: verify non-web project has README documentation.

    Non-web projects (CLI tools, libraries, APIs) especially need usage
    documentation since there's no UI to discover features through.

    Checks for README.md, README.rst, README.txt (case-insensitive).
    Skips for web projects.
    """
    if _is_web_project(ctx.project_dir):
        return None

    if not ctx.project_dir or not os.path.isdir(ctx.project_dir):
        return None

    # Case-insensitive README search
    try:
        entries = os.listdir(ctx.project_dir)
    except OSError:
        return None

    readme_exts = {".md", ".rst", ".txt", ""}
    for fname in entries:
        name_lower = fname.lower()
        if name_lower.startswith("readme"):
            # Check extension
            _, ext = os.path.splitext(name_lower)
            if ext in readme_exts:
                return None

    return (
        "⚠️ NO README: Non-web project has no README documentation. "
        "CLI tools, libraries, and API servers need usage documentation "
        "(README.md) with installation instructions, usage examples, "
        "and API reference."
    )


# ═══════════════════════════════════════════════════════════════════════
# 5. Package Manifest Check (Blocking, gate=done)
# ═══════════════════════════════════════════════════════════════════════

@register_check(3.05, "Package manifest", critical=True, gate="done")
def _check_package_manifest(ctx: CheckContext):
    """Verify non-web project has a package/build manifest.

    Checks for language-specific package manifests:
    - Python: pyproject.toml, setup.py, setup.cfg, requirements.txt, Pipfile
    - Rust: Cargo.toml
    - Go: go.mod
    - Java: pom.xml, build.gradle
    - Ruby: Gemfile
    - C/C++: Makefile, CMakeLists.txt
    - Elixir: mix.exs
    - Swift: Package.swift

    Skips for web projects (they have package.json checks).
    """
    if _is_web_project(ctx.project_dir):
        return None

    if not ctx.project_dir or not os.path.isdir(ctx.project_dir):
        return None

    for manifest in _PACKAGE_MANIFESTS:
        if os.path.isfile(os.path.join(ctx.project_dir, manifest)):
            return None

    return ctx.block(
        "⛔ NO PACKAGE MANIFEST: Non-web project has no recognizable "
        "package or build manifest. Expected one of: pyproject.toml, "
        "setup.py, setup.cfg, requirements.txt, Cargo.toml, go.mod, "
        "Makefile, or equivalent.",
        action=(
            "Create a package manifest for the project. For Python, create "
            "pyproject.toml (preferred) or setup.py. For Go, run 'go mod init'. "
            "For Rust, ensure Cargo.toml exists."
        ),
    )
