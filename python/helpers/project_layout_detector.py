"""
Universal Project Layout Detector — Phase 1 of Universal Infrastructure Fix.

Single source of truth for project framework, language, paths, commands, and
patterns. Replaces ALL hardcoded `src/app/`, `page.tsx`, `npm run build`, etc.
across 12+ validators and the BDD/TDD generation pipeline.

Detection priority:
  1. content_manifest.json tech_stack (available in Phase 0, pre-scaffold)
  2. Framework-specific config file markers (post-scaffold)
  3. File extension scan (fallback)

Supports ALL major frameworks:
  Web:     Next.js (App/Pages), Vite+React, Vite+Vue, Vite+Svelte, Nuxt,
           SvelteKit, Astro, Remix, Angular
  Backend: Flask, Django, FastAPI, Go, Rust, Ruby on Rails, Sinatra
  Mobile:  Swift/iOS (Xcode & SPM), Android/Kotlin (Gradle), React Native,
           Flutter
  Other:   Static HTML, Monorepo, Unknown
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("project_layout_detector")

from python.helpers.source_scanner import list_project_files, EXCLUDE_DIRS


# ─── Data Model ──────────────────────────────────────────────────────────


@dataclass
class ProjectLayout:
    """Describes a project's framework, structure, commands, and conventions.

    All fields have sensible defaults so callers always get a valid object,
    even for unknown or empty projects.
    """

    framework: str = "unknown"
    """Framework identifier: 'nextjs-app', 'vite-react', 'flask', 'go', etc."""

    language: str = "unknown"
    """Primary language: 'typescript', 'python', 'go', 'rust', 'ruby', etc."""

    source_dirs: List[str] = field(default_factory=list)
    """Project-relative source directories (e.g., ['src/app', 'src/components'])."""

    css_files: List[str] = field(default_factory=list)
    """Project-relative CSS file paths discovered on disk."""

    page_pattern: str = ""
    """Glob-like pattern for page files: 'page.tsx', '+page.svelte', '*.vue'."""

    route_pattern: str = ""
    """Glob-like pattern for API route files: 'route.ts', '+server.ts', '*.py'."""

    config_files: List[str] = field(default_factory=list)
    """All detected config files (project-relative)."""

    build_command: str = ""
    """Build command: 'npm run build', 'cargo build', 'go build ./...', etc."""

    test_command: str = ""
    """Test command: 'npm test', 'pytest', 'go test ./...', etc."""

    type_check_cmd: str = ""
    """Type check command: 'npx tsc --noEmit', 'mypy .', 'cargo check', etc."""

    package_manager: str = ""
    """Package manager: 'npm', 'yarn', 'pnpm', 'bun', 'pip', 'cargo', etc."""

    env_file: str = ".env"
    """Default environment file name: '.env.local', '.env', etc."""

    test_dir: str = ""
    """Test directory convention: '__tests__', 'tests', 'spec', 'test', etc."""

    global_css: str = ""
    """Primary global CSS file path (project-relative)."""


# ─── Framework Names Constant (single source of truth) ──────────────────
# All known framework identifiers returned by detect_layout().
# Used by env validator, mise manager, architect gate checks, auto-detect, etc.
FRAMEWORK_NAMES: frozenset[str] = frozenset({
    # Web frontends
    "nextjs-app", "nextjs-pages", "vite-react", "vite-vue",
    "vite-svelte", "nuxt", "sveltekit", "astro", "remix",
    "angular", "react-native", "static-html",
    # Backend
    "flask", "django", "fastapi", "python",
    "go", "rust", "rails", "sinatra",
    # Mobile
    "flutter", "android", "swift-ios", "swift-package",
    # Fallback
    "unknown",
})

# ─── System 5 (ADR-82): Manifest access via shared parse_manifest() ──────────
# Previously had duplicate _MANIFEST_SEARCH_PATHS + _find_manifest() with its
# own json.load. Now delegates to parse_manifest() in _detect_from_manifest().


# ─── Package Manager Detection ───────────────────────────────────────────

_LOCKFILE_TO_PM = {
    "bun.lockb": "bun",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "package-lock.json": "npm",
}


def _detect_package_manager(project_dir: str) -> str:
    """Detect JS/TS package manager from lockfile. Default: 'npm'."""
    for lockfile, pm in _LOCKFILE_TO_PM.items():
        if os.path.exists(os.path.join(project_dir, lockfile)):
            return pm
    return "npm"


def _pm_run(pm: str) -> str:
    """Return the 'run build' command prefix for a JS package manager."""
    if pm == "npm":
        return "npm run build"
    if pm == "yarn":
        return "yarn build"
    if pm == "pnpm":
        return "pnpm build"
    if pm == "bun":
        return "bun run build"
    return "npm run build"


def _pm_test(pm: str) -> str:
    """Return the test command for a JS package manager."""
    if pm == "npm":
        return "npm test"
    if pm == "yarn":
        return "yarn test"
    if pm == "pnpm":
        return "pnpm test"
    if pm == "bun":
        return "bun test"
    return "npm test"


# ─── CSS File Discovery ──────────────────────────────────────────────────

_CSS_EXTENSIONS = {".css", ".scss", ".sass", ".less"}
_CSS_SKIP_DIRS = {"node_modules", ".next", "dist", "build", ".git", "__pycache__", "venv", ".venv", "target"}


def _discover_css_files(project_dir: str, max_depth: int = 4) -> List[str]:
    """Find all CSS files in the project, up to max_depth."""
    # OVL-3: Use centralized scanner instead of inline os.walk
    try:
        abs_paths = list_project_files(
            project_dir,
            extensions=_CSS_EXTENSIONS,
            skip_dirs=EXCLUDE_DIRS | _CSS_SKIP_DIRS,
        )
    except OSError:
        return []

    css_files: List[str] = []
    for fpath in abs_paths:
        rel = os.path.relpath(fpath, project_dir)
        depth = rel.count(os.sep)
        if depth < max_depth:
            css_files.append(rel)
    return sorted(css_files)


# ─── Source Directory Discovery ──────────────────────────────────────────

_SOURCE_SKIP_DIRS = {
    "node_modules", ".next", "dist", "build", ".git", "__pycache__",
    "venv", ".venv", "target", ".agix.proj", "docs", ".github",
    ".svelte-kit", ".nuxt", ".astro",
}


def _discover_source_dirs(project_dir: str, extensions: set[str], max_depth: int = 3) -> List[str]:
    """Find directories containing source files with the given extensions."""
    # OVL-3: Use centralized scanner instead of inline os.walk
    try:
        abs_paths = list_project_files(
            project_dir,
            extensions=extensions,
            skip_dirs=EXCLUDE_DIRS | _SOURCE_SKIP_DIRS,
        )
    except OSError:
        return []

    source_dirs: set[str] = set()
    for fpath in abs_paths:
        rel_dir = os.path.relpath(os.path.dirname(fpath), project_dir)
        if rel_dir == ".":
            continue  # Don't add project root as a source dir
        depth = rel_dir.count(os.sep)
        if depth < max_depth:
            source_dirs.add(rel_dir)
    return sorted(source_dirs)


# ─── Config File Discovery ───────────────────────────────────────────────

_KNOWN_CONFIG_FILES = {
    # JS/TS ecosystem
    "package.json", "tsconfig.json", "tsconfig.app.json",
    "next.config.js", "next.config.mjs", "next.config.ts",
    "vite.config.ts", "vite.config.js", "vite.config.mjs",
    "nuxt.config.ts", "nuxt.config.js",
    "svelte.config.js", "svelte.config.ts",
    "astro.config.mjs", "astro.config.ts",
    "angular.json",
    "tailwind.config.js", "tailwind.config.ts", "tailwind.config.mjs",
    "postcss.config.js", "postcss.config.mjs", "postcss.config.cjs",
    "eslint.config.js", "eslint.config.mjs",
    ".prettierrc", ".prettierrc.json",
    # Python ecosystem
    "pyproject.toml", "setup.py", "setup.cfg",
    "requirements.txt", "Pipfile", "poetry.lock",
    # Go
    "go.mod", "go.sum",
    # Rust
    "Cargo.toml", "Cargo.lock",
    # Ruby
    "Gemfile", "Gemfile.lock", "Rakefile",
    # Mobile
    "pubspec.yaml",  # Flutter/Dart
    "Package.swift",  # Swift SPM
    # Build/CI
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "Makefile", ".github/workflows",
    # Environment
    ".env", ".env.local", ".env.example", ".env.development",
    # Data / ORM
    "prisma/schema.prisma", "drizzle.config.ts",
}


def _discover_config_files(project_dir: str) -> List[str]:
    """Find known config files in the project root."""
    found: List[str] = []
    for cfg in sorted(_KNOWN_CONFIG_FILES):
        if os.path.exists(os.path.join(project_dir, cfg)):
            found.append(cfg)
    return found


# ─── Helper: read package.json dependencies ──────────────────────────────

def _read_pkg_deps(project_dir: str) -> Dict[str, str]:
    """Read all dependencies from package.json (deps + devDeps)."""
    pkg_path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(pkg_path):
        return {}
    try:
        with open(pkg_path, "r") as f:
            pkg = json.load(f)
        deps = dict(pkg.get("dependencies", {}))
        deps.update(pkg.get("devDependencies", {}))
        return deps
    except (json.JSONDecodeError, IOError):
        return {}


def _read_pkg_json(project_dir: str) -> Dict[str, Any]:
    """Read and parse package.json."""
    pkg_path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(pkg_path):
        return {}
    try:
        with open(pkg_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# ─── Helper: read text file for keyword matching ─────────────────────────

def _read_file_content(path: str, max_bytes: int = 4096) -> str:
    """Read file content up to max_bytes. Returns empty string on error."""
    try:
        with open(path, "r", errors="replace") as f:
            return f.read(max_bytes)
    except (IOError, OSError):
        return ""


# ─── Framework-specific detectors ────────────────────────────────────────
# Each returns a ProjectLayout or None if not applicable.
# Priority: more specific frameworks first (e.g., SvelteKit before Vite+Svelte).


def _detect_nextjs(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Next.js (App Router vs Pages Router)."""
    has_config = any(
        os.path.exists(os.path.join(project_dir, f))
        for f in ("next.config.js", "next.config.mjs", "next.config.ts")
    )
    if not has_config:
        return None

    pm = _detect_package_manager(project_dir)

    # Distinguish App Router vs Pages Router
    has_app_dir = (
        os.path.isdir(os.path.join(project_dir, "src", "app"))
        or os.path.isdir(os.path.join(project_dir, "app"))
    )
    has_pages_dir = (
        os.path.isdir(os.path.join(project_dir, "pages"))
        or os.path.isdir(os.path.join(project_dir, "src", "pages"))
    )

    if has_app_dir:
        # App Router
        app_dir = "src/app" if os.path.isdir(os.path.join(project_dir, "src", "app")) else "app"
        css_files = _discover_css_files(project_dir)
        source_dirs = _discover_source_dirs(project_dir, {".tsx", ".ts", ".jsx", ".js"})
        if app_dir not in source_dirs:
            source_dirs.insert(0, app_dir)

        global_css = ""
        for css in css_files:
            if "globals.css" in css or "global.css" in css:
                global_css = css
                break

        return ProjectLayout(
            framework="nextjs-app",
            language="typescript",
            source_dirs=source_dirs,
            css_files=css_files,
            page_pattern="page.tsx",
            route_pattern="route.ts",
            config_files=_discover_config_files(project_dir),
            build_command=_pm_run(pm),
            test_command=_pm_test(pm),
            type_check_cmd="npx tsc --noEmit",
            package_manager=pm,
            env_file=".env.local",
            test_dir="__tests__",
            global_css=global_css,
        )
    elif has_pages_dir:
        # Pages Router
        pages_dir = "pages" if os.path.isdir(os.path.join(project_dir, "pages")) else "src/pages"
        css_files = _discover_css_files(project_dir)
        source_dirs = _discover_source_dirs(project_dir, {".tsx", ".ts", ".jsx", ".js"})
        if pages_dir not in source_dirs:
            source_dirs.insert(0, pages_dir)

        return ProjectLayout(
            framework="nextjs-pages",
            language="typescript",
            source_dirs=source_dirs,
            css_files=css_files,
            page_pattern="index.tsx",
            route_pattern="[...].ts",
            config_files=_discover_config_files(project_dir),
            build_command=_pm_run(pm),
            test_command=_pm_test(pm),
            type_check_cmd="npx tsc --noEmit",
            package_manager=pm,
            env_file=".env.local",
            test_dir="__tests__",
        )
    else:
        # Next.js config exists but no app/ or pages/ — assume App Router (pre-scaffold)
        return ProjectLayout(
            framework="nextjs-app",
            language="typescript",
            source_dirs=[],
            css_files=_discover_css_files(project_dir),
            page_pattern="page.tsx",
            route_pattern="route.ts",
            config_files=_discover_config_files(project_dir),
            build_command=_pm_run(pm),
            test_command=_pm_test(pm),
            type_check_cmd="npx tsc --noEmit",
            package_manager=pm,
            env_file=".env.local",
            test_dir="__tests__",
        )


def _detect_nuxt(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Nuxt.js (nuxt.config.* present)."""
    has_config = any(
        os.path.exists(os.path.join(project_dir, f))
        for f in ("nuxt.config.ts", "nuxt.config.js")
    )
    if not has_config:
        return None

    pm = _detect_package_manager(project_dir)
    source_dirs = _discover_source_dirs(project_dir, {".vue", ".ts", ".js"})
    if "pages" not in source_dirs and os.path.isdir(os.path.join(project_dir, "pages")):
        source_dirs.insert(0, "pages")

    return ProjectLayout(
        framework="nuxt",
        language="typescript",
        source_dirs=source_dirs,
        css_files=_discover_css_files(project_dir),
        page_pattern="*.vue",
        route_pattern="*.ts",
        config_files=_discover_config_files(project_dir),
        build_command=_pm_run(pm),
        test_command=_pm_test(pm),
        type_check_cmd="npx vue-tsc --noEmit",
        package_manager=pm,
        env_file=".env",
        test_dir="tests",
    )


def _detect_sveltekit(project_dir: str) -> Optional[ProjectLayout]:
    """Detect SvelteKit (svelte.config.* present)."""
    has_config = any(
        os.path.exists(os.path.join(project_dir, f))
        for f in ("svelte.config.js", "svelte.config.ts")
    )
    if not has_config:
        return None

    pm = _detect_package_manager(project_dir)
    source_dirs = _discover_source_dirs(project_dir, {".svelte", ".ts", ".js"})
    if "src/routes" not in source_dirs and os.path.isdir(os.path.join(project_dir, "src", "routes")):
        source_dirs.insert(0, "src/routes")

    return ProjectLayout(
        framework="sveltekit",
        language="typescript",
        source_dirs=source_dirs,
        css_files=_discover_css_files(project_dir),
        page_pattern="+page.svelte",
        route_pattern="+server.ts",
        config_files=_discover_config_files(project_dir),
        build_command=_pm_run(pm),
        test_command=_pm_test(pm),
        type_check_cmd="npx svelte-check",
        package_manager=pm,
        env_file=".env",
        test_dir="tests",
    )


def _detect_astro(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Astro (astro.config.* present)."""
    has_config = any(
        os.path.exists(os.path.join(project_dir, f))
        for f in ("astro.config.mjs", "astro.config.ts", "astro.config.js")
    )
    if not has_config:
        return None

    pm = _detect_package_manager(project_dir)
    source_dirs = _discover_source_dirs(project_dir, {".astro", ".tsx", ".ts", ".jsx", ".js"})
    if "src/pages" not in source_dirs and os.path.isdir(os.path.join(project_dir, "src", "pages")):
        source_dirs.insert(0, "src/pages")

    return ProjectLayout(
        framework="astro",
        language="typescript",
        source_dirs=source_dirs,
        css_files=_discover_css_files(project_dir),
        page_pattern="*.astro",
        route_pattern="*.ts",
        config_files=_discover_config_files(project_dir),
        build_command=_pm_run(pm),
        test_command=_pm_test(pm),
        type_check_cmd="npx astro check",
        package_manager=pm,
        env_file=".env",
        test_dir="tests",
    )


def _detect_angular(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Angular (angular.json present)."""
    if not os.path.exists(os.path.join(project_dir, "angular.json")):
        return None

    pm = _detect_package_manager(project_dir)
    source_dirs = _discover_source_dirs(project_dir, {".ts", ".html", ".css", ".scss"})

    return ProjectLayout(
        framework="angular",
        language="typescript",
        source_dirs=source_dirs,
        css_files=_discover_css_files(project_dir),
        page_pattern="*.component.ts",
        route_pattern="*.module.ts",
        config_files=_discover_config_files(project_dir),
        build_command="ng build",
        test_command="ng test",
        type_check_cmd="npx tsc --noEmit",
        package_manager=pm,
        env_file=".env",
        test_dir="src/app",
    )


def _detect_remix(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Remix (@remix-run/* in dependencies)."""
    deps = _read_pkg_deps(project_dir)
    if "@remix-run/react" not in deps and "@remix-run/node" not in deps:
        return None

    pm = _detect_package_manager(project_dir)
    source_dirs = _discover_source_dirs(project_dir, {".tsx", ".ts", ".jsx", ".js"})
    if "app/routes" not in source_dirs and os.path.isdir(os.path.join(project_dir, "app", "routes")):
        source_dirs.insert(0, "app/routes")
    if "app" not in source_dirs and os.path.isdir(os.path.join(project_dir, "app")):
        source_dirs.insert(0, "app")

    return ProjectLayout(
        framework="remix",
        language="typescript",
        source_dirs=source_dirs,
        css_files=_discover_css_files(project_dir),
        page_pattern="*.tsx",
        route_pattern="*.tsx",
        config_files=_discover_config_files(project_dir),
        build_command=_pm_run(pm),
        test_command=_pm_test(pm),
        type_check_cmd="npx tsc --noEmit",
        package_manager=pm,
        env_file=".env",
        test_dir="__tests__",
    )


def _detect_react_native(project_dir: str) -> Optional[ProjectLayout]:
    """Detect React Native (react-native in package.json)."""
    deps = _read_pkg_deps(project_dir)
    if "react-native" not in deps:
        return None

    pm = _detect_package_manager(project_dir)
    return ProjectLayout(
        framework="react-native",
        language="typescript",
        source_dirs=_discover_source_dirs(project_dir, {".tsx", ".ts", ".jsx", ".js"}),
        css_files=[],
        page_pattern="*.tsx",
        route_pattern="*.tsx",
        config_files=_discover_config_files(project_dir),
        build_command=_pm_run(pm),
        test_command=_pm_test(pm),
        type_check_cmd="npx tsc --noEmit",
        package_manager=pm,
        env_file=".env",
        test_dir="__tests__",
    )


def _detect_vite(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Vite projects (vite.config.* present). Distinguishes React/Vue/Svelte."""
    has_config = any(
        os.path.exists(os.path.join(project_dir, f))
        for f in ("vite.config.ts", "vite.config.js", "vite.config.mjs")
    )
    if not has_config:
        return None

    pm = _detect_package_manager(project_dir)
    deps = _read_pkg_deps(project_dir)

    # Determine UI library
    if "vue" in deps:
        framework = "vite-vue"
        page_pattern = "*.vue"
        extensions = {".vue", ".ts", ".js"}
    elif "svelte" in deps:
        framework = "vite-svelte"
        page_pattern = "*.svelte"
        extensions = {".svelte", ".ts", ".js"}
    else:
        # Default to React (most common Vite usage)
        framework = "vite-react"
        page_pattern = "*.tsx"
        extensions = {".tsx", ".ts", ".jsx", ".js"}

    source_dirs = _discover_source_dirs(project_dir, extensions)
    css_files = _discover_css_files(project_dir)

    return ProjectLayout(
        framework=framework,
        language="typescript",
        source_dirs=source_dirs,
        css_files=css_files,
        page_pattern=page_pattern,
        route_pattern="*.ts",
        config_files=_discover_config_files(project_dir),
        build_command=_pm_run(pm),
        test_command=_pm_test(pm),
        type_check_cmd="npx tsc --noEmit",
        package_manager=pm,
        env_file=".env",
        test_dir="__tests__",
    )


# ─── Backend / Systems Detectors ─────────────────────────────────────────


def _detect_go(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Go projects (go.mod present)."""
    if not os.path.exists(os.path.join(project_dir, "go.mod")):
        return None

    source_dirs = _discover_source_dirs(project_dir, {".go"})
    return ProjectLayout(
        framework="go",
        language="go",
        source_dirs=source_dirs,
        css_files=[],
        page_pattern="*.go",
        route_pattern="*.go",
        config_files=_discover_config_files(project_dir),
        build_command="go build ./...",
        test_command="go test ./...",
        type_check_cmd="go vet ./...",
        package_manager="go",
        env_file=".env",
        test_dir=".",  # Go tests are colocated
    )


def _detect_rust(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Rust projects (Cargo.toml present)."""
    if not os.path.exists(os.path.join(project_dir, "Cargo.toml")):
        return None

    source_dirs = _discover_source_dirs(project_dir, {".rs"})
    if "src" not in source_dirs and os.path.isdir(os.path.join(project_dir, "src")):
        source_dirs.insert(0, "src")

    return ProjectLayout(
        framework="rust",
        language="rust",
        source_dirs=source_dirs,
        css_files=[],
        page_pattern="*.rs",
        route_pattern="*.rs",
        config_files=_discover_config_files(project_dir),
        build_command="cargo build",
        test_command="cargo test",
        type_check_cmd="cargo check",
        package_manager="cargo",
        env_file=".env",
        test_dir="tests",
    )


def _detect_flutter(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Flutter/Dart projects (pubspec.yaml present)."""
    if not os.path.exists(os.path.join(project_dir, "pubspec.yaml")):
        return None

    source_dirs = _discover_source_dirs(project_dir, {".dart"})
    if "lib" not in source_dirs and os.path.isdir(os.path.join(project_dir, "lib")):
        source_dirs.insert(0, "lib")

    return ProjectLayout(
        framework="flutter",
        language="dart",
        source_dirs=source_dirs,
        css_files=[],
        page_pattern="*.dart",
        route_pattern="*.dart",
        config_files=_discover_config_files(project_dir),
        build_command="flutter build",
        test_command="flutter test",
        type_check_cmd="dart analyze",
        package_manager="pub",
        env_file=".env",
        test_dir="test",
    )


def _detect_swift(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Swift projects (*.xcodeproj or Package.swift)."""
    # Check for Xcode project
    has_xcodeproj = False
    try:
        for entry in os.listdir(project_dir):
            if entry.endswith(".xcodeproj"):
                has_xcodeproj = True
                break
    except OSError:
        pass

    has_spm = os.path.exists(os.path.join(project_dir, "Package.swift"))

    if not has_xcodeproj and not has_spm:
        return None

    framework = "swift-ios" if has_xcodeproj else "swift-package"
    source_dirs = _discover_source_dirs(project_dir, {".swift"})

    return ProjectLayout(
        framework=framework,
        language="swift",
        source_dirs=source_dirs,
        css_files=[],
        page_pattern="*.swift",
        route_pattern="*.swift",
        config_files=_discover_config_files(project_dir),
        build_command="xcodebuild build" if has_xcodeproj else "swift build",
        test_command="xcodebuild test" if has_xcodeproj else "swift test",
        type_check_cmd="swiftc -typecheck" if has_spm else "",
        package_manager="spm",
        env_file=".env",
        test_dir="Tests" if has_spm else "tests",
    )


def _detect_android(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Android projects (build.gradle with android plugin)."""
    has_gradle_kts = os.path.exists(os.path.join(project_dir, "build.gradle.kts"))
    has_gradle = os.path.exists(os.path.join(project_dir, "build.gradle"))

    if not has_gradle_kts and not has_gradle:
        return None

    # Check if it's actually an Android project (vs generic Java/Kotlin)
    gradle_file = "build.gradle.kts" if has_gradle_kts else "build.gradle"
    content = _read_file_content(os.path.join(project_dir, gradle_file))
    is_android = "android" in content.lower() or "com.android" in content

    # Also check app/ subdir
    if not is_android:
        app_gradle = os.path.join(project_dir, "app", "build.gradle.kts")
        if not os.path.exists(app_gradle):
            app_gradle = os.path.join(project_dir, "app", "build.gradle")
        if os.path.exists(app_gradle):
            app_content = _read_file_content(app_gradle)
            is_android = "android" in app_content.lower() or "com.android" in app_content

    if not is_android:
        return None

    # Detect language (Kotlin vs Java)
    source_dirs = _discover_source_dirs(project_dir, {".kt", ".java"})
    has_kotlin = any(
        os.path.exists(os.path.join(project_dir, "app", "src", "main", d))
        for d in ("kotlin", "java")
    )
    if "app/src/main" not in source_dirs:
        for subdir in ("app/src/main/kotlin", "app/src/main/java"):
            if os.path.isdir(os.path.join(project_dir, subdir)):
                source_dirs.insert(0, "app/src/main")
                break

    return ProjectLayout(
        framework="android",
        language="kotlin",
        source_dirs=source_dirs,
        css_files=[],
        page_pattern="*.kt",
        route_pattern="*.kt",
        config_files=_discover_config_files(project_dir),
        build_command="./gradlew build",
        test_command="./gradlew test",
        type_check_cmd="./gradlew compileKotlin",
        package_manager="gradle",
        env_file=".env",
        test_dir="app/src/test",
    )


def _detect_ruby(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Ruby projects (Gemfile present). Distinguishes Rails vs Sinatra."""
    if not os.path.exists(os.path.join(project_dir, "Gemfile")):
        return None

    gemfile_content = _read_file_content(os.path.join(project_dir, "Gemfile"))
    is_rails = (
        "rails" in gemfile_content.lower()
        and os.path.exists(os.path.join(project_dir, "config", "routes.rb"))
    )

    source_dirs = _discover_source_dirs(project_dir, {".rb", ".erb"})

    if is_rails:
        if "app" not in source_dirs and os.path.isdir(os.path.join(project_dir, "app")):
            source_dirs.insert(0, "app")
        return ProjectLayout(
            framework="rails",
            language="ruby",
            source_dirs=source_dirs,
            css_files=_discover_css_files(project_dir),
            page_pattern="*.html.erb",
            route_pattern="*.rb",
            config_files=_discover_config_files(project_dir),
            build_command="bundle install",
            test_command="bundle exec rspec",
            type_check_cmd="bundle exec rubocop",
            package_manager="bundler",
            env_file=".env",
            test_dir="spec",
        )
    else:
        # Sinatra or generic Ruby
        return ProjectLayout(
            framework="sinatra",
            language="ruby",
            source_dirs=source_dirs,
            css_files=_discover_css_files(project_dir),
            page_pattern="*.erb",
            route_pattern="*.rb",
            config_files=_discover_config_files(project_dir),
            build_command="bundle install",
            test_command="bundle exec rspec",
            type_check_cmd="bundle exec rubocop",
            package_manager="bundler",
            env_file=".env",
            test_dir="spec",
        )


def _detect_python(project_dir: str) -> Optional[ProjectLayout]:
    """Detect Python projects. Distinguishes Django, FastAPI, Flask."""
    has_manage_py = os.path.exists(os.path.join(project_dir, "manage.py"))
    has_pyproject = os.path.exists(os.path.join(project_dir, "pyproject.toml"))
    has_requirements = os.path.exists(os.path.join(project_dir, "requirements.txt"))
    has_setup_py = os.path.exists(os.path.join(project_dir, "setup.py"))
    has_pipfile = os.path.exists(os.path.join(project_dir, "Pipfile"))

    if not any([has_manage_py, has_pyproject, has_requirements, has_setup_py, has_pipfile]):
        return None

    source_dirs = _discover_source_dirs(project_dir, {".py"})

    # Detect sub-framework
    framework = "python"

    if has_manage_py:
        framework = "django"
        test_command = "python manage.py test"
    else:
        test_command = "pytest"

        # Check for FastAPI/Flask in requirements or pyproject
        deps_text = ""
        if has_requirements:
            deps_text += _read_file_content(os.path.join(project_dir, "requirements.txt"))
        if has_pyproject:
            deps_text += _read_file_content(os.path.join(project_dir, "pyproject.toml"))

        deps_lower = deps_text.lower()
        if "fastapi" in deps_lower:
            framework = "fastapi"
        elif "flask" in deps_lower:
            framework = "flask"

    return ProjectLayout(
        framework=framework,
        language="python",
        source_dirs=source_dirs,
        css_files=[],
        page_pattern="*.py",
        route_pattern="*.py",
        config_files=_discover_config_files(project_dir),
        build_command="pip install -r requirements.txt" if has_requirements else "pip install -e .",
        test_command=test_command,
        type_check_cmd="mypy .",
        package_manager="pip",
        env_file=".env",
        test_dir="tests",
    )


def _detect_static_html(project_dir: str) -> Optional[ProjectLayout]:
    """Detect plain HTML/CSS/JS projects (index.html at root)."""
    if not os.path.exists(os.path.join(project_dir, "index.html")):
        return None

    return ProjectLayout(
        framework="static-html",
        language="javascript",
        source_dirs=["."],
        css_files=_discover_css_files(project_dir),
        page_pattern="*.html",
        route_pattern="",
        config_files=_discover_config_files(project_dir),
        build_command="",
        test_command="",
        type_check_cmd="",
        package_manager="",
        env_file=".env",
        test_dir="tests",
    )


# ─── Manifest-Based Detection (Pre-Scaffold) ─────────────────────────────

# Maps manifest tech_stack keywords to (framework, language) tuples.
_MANIFEST_FRAMEWORK_MAP: List[tuple[re.Pattern, str, str]] = [
    # Order matters: more specific first
    (re.compile(r"\bnuxt\b", re.I), "nuxt", "typescript"),
    (re.compile(r"\bsveltekit\b|\bsvelte\s*kit\b", re.I), "sveltekit", "typescript"),
    (re.compile(r"\bsvelte\b", re.I), "vite-svelte", "typescript"),
    (re.compile(r"\bastro\b", re.I), "astro", "typescript"),
    (re.compile(r"\bremix\b", re.I), "remix", "typescript"),
    (re.compile(r"\bangular\b", re.I), "angular", "typescript"),
    (re.compile(r"\bnext\.?js\b|\bnext\b", re.I), "nextjs-app", "typescript"),
    (re.compile(r"\bvue\b", re.I), "vite-vue", "typescript"),
    (re.compile(r"\breact\s*native\b", re.I), "react-native", "typescript"),
    (re.compile(r"\bvite\b|\breact\b", re.I), "vite-react", "typescript"),
    (re.compile(r"\bflutter\b", re.I), "flutter", "dart"),
    (re.compile(r"\bandroid\b", re.I), "android", "kotlin"),
    (re.compile(r"\bswift\b|\bios\b|\bxcode\b", re.I), "swift-ios", "swift"),
    (re.compile(r"\bdjango\b", re.I), "django", "python"),
    (re.compile(r"\bfastapi\b|\bfast\s*api\b", re.I), "fastapi", "python"),
    (re.compile(r"\bflask\b", re.I), "flask", "python"),
    (re.compile(r"\brails\b|\bruby\s+on\s+rails\b", re.I), "rails", "ruby"),
    (re.compile(r"\bsinatra\b", re.I), "sinatra", "ruby"),
    (re.compile(r"\bruby\b", re.I), "rails", "ruby"),  # Default Ruby → Rails
    (re.compile(r"\brust\b|\baxum\b|\bactix\b|\brocket\b", re.I), "rust", "rust"),
    (re.compile(r"\bgo\b|\bgolang\b|\bchi\b|\bgin\b|\bfiber\b", re.I), "go", "go"),
    (re.compile(r"\bpython\b", re.I), "python", "python"),
]


def _detect_from_manifest(project_dir: str) -> Optional[ProjectLayout]:
    """Detect framework from content_manifest.json (pre-scaffold).

    System 5 (ADR-82): Uses shared parse_manifest() instead of deleted
    _find_manifest() which had its own json.load.
    """
    from python.helpers.manifest_parser import parse_manifest
    manifest = parse_manifest(project_dir)
    tech_stack = manifest.tech_stack
    if not tech_stack:
        return None

    # Build a search string from tech_stack
    if isinstance(tech_stack, dict):
        search_text = " ".join(str(v) for v in tech_stack.values())
    elif isinstance(tech_stack, list):
        search_text = " ".join(str(t) for t in tech_stack)
    else:
        search_text = str(tech_stack)

    if not search_text.strip():
        return None

    for pattern, framework, language in _MANIFEST_FRAMEWORK_MAP:
        if pattern.search(search_text):
            # Return a default layout for this framework
            # Callers will get enriched data once files exist
            return _build_default_layout(framework, language, project_dir)

    return None


def _build_default_layout(framework: str, language: str, project_dir: str) -> ProjectLayout:
    """Build a ProjectLayout with defaults for a given framework/language.

    Used when detecting from manifest (pre-scaffold) where actual files
    may not exist yet.
    """
    # Framework → defaults mapping
    _DEFAULTS: Dict[str, Dict[str, Any]] = {
        "nextjs-app": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx tsc --noEmit", "env": ".env.local", "td": "__tests__",
            "pp": "page.tsx", "rp": "route.ts",
        },
        "nextjs-pages": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx tsc --noEmit", "env": ".env.local", "td": "__tests__",
            "pp": "index.tsx", "rp": "[...].ts",
        },
        "vite-react": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx tsc --noEmit", "env": ".env", "td": "__tests__",
            "pp": "*.tsx", "rp": "*.ts",
        },
        "vite-vue": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx vue-tsc --noEmit", "env": ".env", "td": "__tests__",
            "pp": "*.vue", "rp": "*.ts",
        },
        "vite-svelte": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx svelte-check", "env": ".env", "td": "__tests__",
            "pp": "*.svelte", "rp": "*.ts",
        },
        "nuxt": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx vue-tsc --noEmit", "env": ".env", "td": "tests",
            "pp": "*.vue", "rp": "*.ts",
        },
        "sveltekit": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx svelte-check", "env": ".env", "td": "tests",
            "pp": "+page.svelte", "rp": "+server.ts",
        },
        "astro": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx astro check", "env": ".env", "td": "tests",
            "pp": "*.astro", "rp": "*.ts",
        },
        "remix": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx tsc --noEmit", "env": ".env", "td": "__tests__",
            "pp": "*.tsx", "rp": "*.tsx",
        },
        "angular": {
            "pm": "npm", "build": "ng build", "test": "ng test",
            "tc": "npx tsc --noEmit", "env": ".env", "td": "src/app",
            "pp": "*.component.ts", "rp": "*.module.ts",
        },
        "react-native": {
            "pm": "npm", "build": "npm run build", "test": "npm test",
            "tc": "npx tsc --noEmit", "env": ".env", "td": "__tests__",
            "pp": "*.tsx", "rp": "*.tsx",
        },
        "flask": {
            "pm": "pip", "build": "pip install -r requirements.txt", "test": "pytest",
            "tc": "mypy .", "env": ".env", "td": "tests",
            "pp": "*.py", "rp": "*.py",
        },
        "django": {
            "pm": "pip", "build": "pip install -r requirements.txt", "test": "python manage.py test",
            "tc": "mypy .", "env": ".env", "td": "tests",
            "pp": "*.py", "rp": "*.py",
        },
        "fastapi": {
            "pm": "pip", "build": "pip install -r requirements.txt", "test": "pytest",
            "tc": "mypy .", "env": ".env", "td": "tests",
            "pp": "*.py", "rp": "*.py",
        },
        "python": {
            "pm": "pip", "build": "pip install -r requirements.txt", "test": "pytest",
            "tc": "mypy .", "env": ".env", "td": "tests",
            "pp": "*.py", "rp": "*.py",
        },
        "go": {
            "pm": "go", "build": "go build ./...", "test": "go test ./...",
            "tc": "go vet ./...", "env": ".env", "td": ".",
            "pp": "*.go", "rp": "*.go",
        },
        "rust": {
            "pm": "cargo", "build": "cargo build", "test": "cargo test",
            "tc": "cargo check", "env": ".env", "td": "tests",
            "pp": "*.rs", "rp": "*.rs",
        },
        "rails": {
            "pm": "bundler", "build": "bundle install", "test": "bundle exec rspec",
            "tc": "bundle exec rubocop", "env": ".env", "td": "spec",
            "pp": "*.html.erb", "rp": "*.rb",
        },
        "sinatra": {
            "pm": "bundler", "build": "bundle install", "test": "bundle exec rspec",
            "tc": "bundle exec rubocop", "env": ".env", "td": "spec",
            "pp": "*.erb", "rp": "*.rb",
        },
        "flutter": {
            "pm": "pub", "build": "flutter build", "test": "flutter test",
            "tc": "dart analyze", "env": ".env", "td": "test",
            "pp": "*.dart", "rp": "*.dart",
        },
        "android": {
            "pm": "gradle", "build": "./gradlew build", "test": "./gradlew test",
            "tc": "./gradlew compileKotlin", "env": ".env", "td": "app/src/test",
            "pp": "*.kt", "rp": "*.kt",
        },
        "swift-ios": {
            "pm": "spm", "build": "xcodebuild build", "test": "xcodebuild test",
            "tc": "", "env": ".env", "td": "tests",
            "pp": "*.swift", "rp": "*.swift",
        },
        "swift-package": {
            "pm": "spm", "build": "swift build", "test": "swift test",
            "tc": "swiftc -typecheck", "env": ".env", "td": "Tests",
            "pp": "*.swift", "rp": "*.swift",
        },
    }

    defaults = _DEFAULTS.get(framework, {})
    pm = defaults.get("pm", "")
    # If project dir has lockfiles, override PM
    if os.path.isdir(project_dir) and pm in ("npm", ""):
        detected_pm = _detect_package_manager(project_dir)
        if detected_pm != "npm" or pm == "npm":
            pm = detected_pm

    return ProjectLayout(
        framework=framework,
        language=language,
        source_dirs=[],  # No files yet in pre-scaffold
        css_files=_discover_css_files(project_dir),
        page_pattern=defaults.get("pp", ""),
        route_pattern=defaults.get("rp", ""),
        config_files=_discover_config_files(project_dir),
        build_command=defaults.get("build", ""),
        test_command=defaults.get("test", ""),
        type_check_cmd=defaults.get("tc", ""),
        package_manager=pm,
        env_file=defaults.get("env", ".env"),
        test_dir=defaults.get("td", "tests"),
    )


# ─── Main Detection Entry Point ──────────────────────────────────────────

# Detection chain: more specific frameworks first.
# Each detector returns a ProjectLayout or None.
_FILE_DETECTORS = [
    _detect_nuxt,          # Must be before Vite (Nuxt can include Vite)
    _detect_sveltekit,     # Must be before Vite (SvelteKit uses Vite internally)
    _detect_astro,         # Must be before Vite (Astro uses Vite internally)
    _detect_angular,       # Has unique angular.json
    _detect_nextjs,        # Must be before generic Vite
    _detect_remix,         # Dependency-based (must be before generic Vite/React)
    _detect_react_native,  # Must be before generic Vite/React
    _detect_vite,          # Catch-all for Vite+React/Vue/Svelte
    _detect_flutter,       # pubspec.yaml
    _detect_swift,         # *.xcodeproj or Package.swift
    _detect_android,       # build.gradle with android plugin
    _detect_ruby,          # Gemfile (Rails vs Sinatra)
    _detect_go,            # go.mod
    _detect_rust,          # Cargo.toml
    _detect_python,        # pyproject.toml, requirements.txt, manage.py
    _detect_static_html,   # index.html (last resort for web)
]


def detect_layout(project_dir: str) -> ProjectLayout:
    """Detect project framework, language, paths, and commands.

    Detection priority:
      1. File-based detection (most specific — actual config files on disk)
      2. Manifest-based detection (content_manifest.json tech_stack)
      3. Unknown fallback

    File-based detection runs FIRST because it can distinguish sub-frameworks
    (e.g., Next.js App Router vs Pages Router) that manifest text can't.
    Manifest is used as fallback for pre-scaffold projects.

    Args:
        project_dir: Absolute path to the project root directory.

    Returns:
        ProjectLayout with all fields populated. Returns a layout with
        framework='unknown' for empty/unrecognized projects.
    """
    if not project_dir or not os.path.isdir(project_dir):
        return ProjectLayout()

    # Priority 1: File-based detection (most specific)
    for detector in _FILE_DETECTORS:
        try:
            result = detector(project_dir)
            if result is not None:
                logger.info(
                    f"[LAYOUT DETECTOR] Detected: framework={result.framework}, "
                    f"language={result.language}, pm={result.package_manager}"
                )
                return result
        except Exception as e:
            logger.warning(f"[LAYOUT DETECTOR] Detector {detector.__name__} failed: {e}")
            continue

    # Priority 2: Manifest-based detection (pre-scaffold)
    try:
        manifest_result = _detect_from_manifest(project_dir)
        if manifest_result is not None:
            logger.info(
                f"[LAYOUT DETECTOR] Detected from manifest: framework={manifest_result.framework}, "
                f"language={manifest_result.language}"
            )
            return manifest_result
    except Exception as e:
        logger.warning(f"[LAYOUT DETECTOR] Manifest detection failed: {e}")

    # Priority 3: Unknown
    return ProjectLayout(
        config_files=_discover_config_files(project_dir),
        css_files=_discover_css_files(project_dir),
    )
