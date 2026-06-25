"""
Codebase State Injector — Proactive project awareness for subordinate agents.
=============================================================================

Mirrors Roo-Code's <environment_details> pattern (getEnvironmentDetails.ts):
    Every subordinate agent receives a manifest of the project's CURRENT state
    at delegation time. This eliminates the root cause of multi-agent coordination
    failures: agents guessing type names, creating duplicate files, or importing
    symbols that don't exist.

What Roo-Code does (and we mirror):
    1. Full recursive file listing of the workspace
    2. Recently modified files (what changed since last look)
    3. Active terminal state (running processes)
    4. Current mode and context

What WE add (TypeScript/Prisma specific):
    5. Prisma model names → "these are your data models, use THESE names"
    6. Exported TypeScript types → "these types exist at @/types, import THESE"
    7. Existing API routes → "these endpoints already exist"
    8. Config files → "these configs are already set up"

Usage:
    from python.helpers.codebase_state_injector import inject_codebase_state

    # In call_subordinate.py, before sending the delegation message:
    message = inject_codebase_state(project_dir, message)

Architecture:
    scan_project_state(dir)       → structured dict of project state
    format_codebase_manifest(st)  → human-readable manifest string
    inject_codebase_state(dir, m) → message with manifest prepended
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Set
from python.helpers.planning_paths import get_path as _planning_path

from python.helpers.project_layout_detector import detect_layout
from python.helpers.source_scanner import list_project_files, EXCLUDE_DIRS

logger = logging.getLogger("agix.codebase_state")

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

# Directories to skip when scanning (mirrors Roo-Code DIRS_TO_IGNORE)
_SKIP_DIRS = {
    "node_modules", ".next", ".nuxt", "dist", ".git", "__pycache__",
    ".turbo", ".cache", ".vercel", ".output", "coverage", ".svelte-kit",
    "build", ".expo", ".parcel-cache",
}

# File extensions to include in the file listing
_SOURCE_EXTENSIONS = {
    ".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs",
    ".css", ".scss", ".prisma", ".json", ".md",
    ".py", ".env", ".yaml", ".yml", ".toml",
}

# Config file patterns (relative to project root)
_CONFIG_PATTERNS = {
    "package.json", "tsconfig.json", "tsconfig.app.json",
    "next.config.js", "next.config.mjs", "next.config.ts",
    "tailwind.config.js", "tailwind.config.ts", "tailwind.config.mjs",
    "postcss.config.js", "postcss.config.mjs", "postcss.config.cjs",
    "vite.config.ts", "vite.config.js",
    "nuxt.config.ts", "nuxt.config.js",
    ".env", ".env.local", ".env.example",
    "prisma/schema.prisma",
    "drizzle.config.ts",
    "eslint.config.js", "eslint.config.mjs",
    ".prettierrc", ".prettierrc.json",
}

# Maximum files to list (prevents token explosion on huge projects)
_MAX_FILES = 200

# ──────────────────────────────────────────────────────────────────────
# Prisma / TypeScript parsing (reuses type_coherence logic)
# ──────────────────────────────────────────────────────────────────────

_PRISMA_MODEL_RE = re.compile(r"^\s*model\s+(\w+)\s*\{", re.MULTILINE)
_PRISMA_ENUM_RE = re.compile(r"^\s*enum\s+(\w+)\s*\{", re.MULTILINE)

_TS_EXPORT_INTERFACE_RE = re.compile(
    r"^\s*export\s+(?:interface|class)\s+(\w+)", re.MULTILINE
)
_TS_EXPORT_TYPE_RE = re.compile(
    r"^\s*export\s+type\s+(\w+)\s*=", re.MULTILINE
)
_TS_EXPORT_ENUM_RE = re.compile(
    r"^\s*export\s+enum\s+(\w+)", re.MULTILINE
)
_TS_EXPORT_FUNCTION_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE
)
_TS_EXPORT_CONST_RE = re.compile(
    r"^\s*export\s+(?:const|let|var)\s+(\w+)\s*=", re.MULTILINE
)
_TS_REEXPORT_RE = re.compile(
    r"^\s*export\s*\{([^}]+)\}", re.MULTILINE
)


def _extract_prisma_names(schema_path: str) -> List[str]:
    """Extract model and enum names from a Prisma schema file."""
    if not os.path.isfile(schema_path):
        return []
    try:
        with open(schema_path, "r", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError):
        return []

    names = []
    for pattern in (_PRISMA_MODEL_RE, _PRISMA_ENUM_RE):
        for match in pattern.finditer(content):
            names.append(match.group(1))
    return sorted(set(names))


def _extract_ts_exports(types_path: str) -> List[str]:
    """Extract exported type/interface names from a TypeScript file."""
    if not os.path.isfile(types_path):
        return []
    try:
        with open(types_path, "r", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError):
        return []

    exports: Set[str] = set()
    for pattern in (
        _TS_EXPORT_INTERFACE_RE,
        _TS_EXPORT_TYPE_RE,
        _TS_EXPORT_ENUM_RE,
        _TS_EXPORT_FUNCTION_RE,
        _TS_EXPORT_CONST_RE,
    ):
        for match in pattern.finditer(content):
            exports.add(match.group(1))

    for match in _TS_REEXPORT_RE.finditer(content):
        for name in match.group(1).split(","):
            name = name.strip()
            if " as " in name:
                name = name.split(" as ")[1].strip()
            if name and re.match(r"^\w+$", name):
                exports.add(name)

    return sorted(exports)


# ──────────────────────────────────────────────────────────────────────
# Full Definition Extraction (Fix 6 — RCA MSR 1777396305)
# Extracts complete type bodies, not just names, so subordinate agents
# see canonical definitions and can't create conflicting types.
# ──────────────────────────────────────────────────────────────────────

_PRISMA_BLOCK_RE = re.compile(
    r"^\s*(model|enum)\s+(\w+)\s*\{([^}]*)\}",
    re.MULTILINE | re.DOTALL,
)


def extract_prisma_definitions(schema_path: str) -> Dict[str, str]:
    """Extract full model/enum definitions from a Prisma schema file.

    Returns dict mapping name → definition body text.
    E.g. {"BusinessStatus": "  ACTIVE\n  PENDING\n  ...", "Business": "  id  String  @id ..."}
    """
    if not os.path.isfile(schema_path):
        return {}
    try:
        with open(schema_path, "r", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError):
        return {}

    defs: Dict[str, str] = {}
    for match in _PRISMA_BLOCK_RE.finditer(content):
        kind = match.group(1)  # "model" or "enum"
        name = match.group(2)
        body = match.group(3).strip()
        # Reconstruct as readable definition
        defs[name] = f"{kind} {name} {{\n{body}\n}}"
    return defs


# TypeScript definition extraction: captures full bodies between { }
# for interfaces, enums, and type aliases
_TS_DEF_BLOCK_RE = re.compile(
    r"^\s*export\s+(interface|class|enum)\s+(\w+)(?:[^{]*)\{([^}]*)\}",
    re.MULTILINE | re.DOTALL,
)
_TS_TYPE_ALIAS_RE = re.compile(
    r"^\s*export\s+type\s+(\w+)\s*=\s*(.+?);",
    re.MULTILINE | re.DOTALL,
)


def extract_ts_definitions(types_path: str) -> Dict[str, str]:
    """Extract full interface/type/enum definitions from a TypeScript file.

    Returns dict mapping name → definition body text.
    E.g. {"Business": "export interface Business {\n  id: string;\n  ...\n}"}
    """
    if not os.path.isfile(types_path):
        return {}
    try:
        with open(types_path, "r", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError):
        return {}

    defs: Dict[str, str] = {}

    # Interfaces, classes, enums with { body }
    for match in _TS_DEF_BLOCK_RE.finditer(content):
        kind = match.group(1)
        name = match.group(2)
        body = match.group(3).strip()
        defs[name] = f"export {kind} {name} {{\n{body}\n}}"

    # Type aliases: export type X = ...
    for match in _TS_TYPE_ALIAS_RE.finditer(content):
        name = match.group(1)
        value = match.group(2).strip()
        if name not in defs:  # Don't overwrite if already captured as block
            defs[name] = f"export type {name} = {value};"

    return defs


def _find_all_types_files(project_dir: str, layout=None) -> List[str]:
    """Locate all TypeScript type definition files in the project.

    Uses layout.source_dirs to find type directories when available,
    falls back to scanning common patterns:
    - src/types/index.ts, src/types/*.ts
    - types/index.ts, types/*.ts
    - src/types.ts
    """
    found: List[str] = []
    type_dirs = []

    # Use layout source_dirs to discover type directories
    if layout and layout.source_dirs:
        for src_dir in layout.source_dirs:
            candidate = os.path.join(project_dir, src_dir, "types")
            if os.path.isdir(candidate):
                type_dirs.append(candidate)

    # Fallback: hardcoded common paths
    if not type_dirs:
        type_dirs = [
            os.path.join(project_dir, "src", "types"),
            os.path.join(project_dir, "types"),
        ]

    for td in type_dirs:
        if os.path.isdir(td):
            for fname in os.listdir(td):
                if fname.endswith(".ts") and not fname.endswith(".test.ts"):
                    found.append(os.path.join(td, fname))

    # Also check src/types.ts (single file pattern)
    single = os.path.join(project_dir, "src", "types.ts")
    if os.path.isfile(single):
        found.append(single)

    return sorted(set(found))


def _collect_ts_definitions(project_dir: str, layout=None) -> Dict[str, str]:
    """Collect TypeScript type definitions from all types files in the project.

    Merges definitions from all discovered types files. If the same name
    appears in multiple files, the first occurrence wins.
    Caps at 30 definitions to prevent token explosion.
    """
    all_defs: Dict[str, str] = {}
    max_defs = 30

    for types_file in _find_all_types_files(project_dir, layout=layout):
        if len(all_defs) >= max_defs:
            break
        file_defs = extract_ts_definitions(types_file)
        for name, body in file_defs.items():
            if name not in all_defs:
                all_defs[name] = body
                if len(all_defs) >= max_defs:
                    break
    return all_defs



def _find_types_file(project_dir: str, layout=None) -> str:
    """Locate the main types file.

    Uses layout.source_dirs to find type directories when available.
    """
    candidates = []

    # Build candidates from layout source_dirs
    if layout and layout.source_dirs:
        for src_dir in layout.source_dirs:
            candidates.append(os.path.join(project_dir, src_dir, "types", "index.ts"))
            candidates.append(os.path.join(project_dir, src_dir, "types", "index.d.ts"))

    # Fallback: hardcoded common paths
    if not candidates:
        candidates = [
            os.path.join(project_dir, "src", "types", "index.ts"),
            os.path.join(project_dir, "src", "types", "index.d.ts"),
            os.path.join(project_dir, "src", "types.ts"),
            os.path.join(project_dir, "types", "index.ts"),
        ]

    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""


# ──────────────────────────────────────────────────────────────────────
# File tree scanning (mirrors Roo-Code's listFiles with ripgrep)
# ──────────────────────────────────────────────────────────────────────

def _scan_file_tree(project_dir: str, layout=None) -> Dict[str, List[str]]:
    """Scan project directory for source files, API routes, and configs.

    Returns dict with keys:
        existing_files: list of relative paths to source files
        api_routes: list of relative paths to API route files
        config_files: list of relative paths to config files
    """
    existing_files: List[str] = []
    api_routes: List[str] = []
    config_files: List[str] = []

    if not os.path.isdir(project_dir):
        return {"existing_files": [], "api_routes": [], "config_files": []}

    # Determine route file names from layout
    route_filenames = {"route.ts", "route.js", "route.tsx"}  # defaults
    if layout and layout.route_pattern:
        route_filenames.add(layout.route_pattern)

    # OVL-3: Use centralized scanner instead of inline os.walk
    abs_paths = list_project_files(
        project_dir,
        extensions=_SOURCE_EXTENSIONS,
        skip_dirs=EXCLUDE_DIRS | _SKIP_DIRS,
        max_files=_MAX_FILES,
    )

    for fpath in abs_paths:
        fname = os.path.basename(fpath)
        rel_path = os.path.relpath(fpath, project_dir)

        # Classify
        existing_files.append(rel_path)

        # Detect API routes
        if "/api/" in rel_path and fname in route_filenames:
            api_routes.append(rel_path)

        # Detect config files
        if rel_path in _CONFIG_PATTERNS or fname in _CONFIG_PATTERNS:
            config_files.append(rel_path)

    return {
        "existing_files": sorted(existing_files),
        "api_routes": sorted(api_routes),
        "config_files": sorted(config_files),
    }


# ──────────────────────────────────────────────────────────────────────
# Component Export Extraction (Fix 4, ITR-32 RC-E)
# Scans src/components/ for exported names so the manifest can tell
# code agents how to import existing components.
# ──────────────────────────────────────────────────────────────────────

# File extensions that are valid component files
_COMPONENT_EXTENSIONS = {".tsx", ".ts", ".jsx", ".js"}

# File patterns to EXCLUDE from component guide
_COMPONENT_EXCLUDE_PATTERNS = {".test.", ".spec.", ".stories.", ".d.ts"}

# Regex for default export: export default function Foo() / export default class Foo
_DEFAULT_EXPORT_RE = re.compile(
    r"^\s*export\s+default\s+(?:(?:async\s+)?function|class)\s+(\w+)",
    re.MULTILINE,
)


def _extract_component_exports(project_dir: str) -> List[Dict[str, any]]:
    """Extract export names from component files under src/components/.

    Returns a list of dicts:
        [{"import_path": "@/components/Hero", "exports": ["Hero"], "is_default": True}, ...]

    Only scans .tsx/.ts/.jsx/.js files, excluding test/spec/story files.
    Caps at 50 components to prevent token explosion.

    ITR-32 Fix 4 (RC-E): Code agents building landing pages didn't know
    to import existing components because the manifest listed filenames
    only. Now they get ready-to-use import paths.
    """
    components: List[Dict] = []
    max_components = 50

    components_dir = os.path.join(project_dir, "src", "components")
    if not os.path.isdir(components_dir):
        return []

    for root, dirs, files in os.walk(components_dir):
        # Prune ignored dirs
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]

        for fname in files:
            if len(components) >= max_components:
                break

            _, ext = os.path.splitext(fname)
            if ext not in _COMPONENT_EXTENSIONS:
                continue

            # Skip test/spec/story files
            if any(pattern in fname for pattern in _COMPONENT_EXCLUDE_PATTERNS):
                continue

            # Skip index barrel files (they re-export, don't define)
            basename_no_ext = os.path.splitext(fname)[0]
            if basename_no_ext == "index":
                continue

            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    content = f.read(4096)  # Read first 4KB — enough for exports
            except (IOError, OSError):
                continue

            # Extract exports using existing regexes
            exports = set()
            is_default = False

            # Default export
            for match in _DEFAULT_EXPORT_RE.finditer(content):
                exports.add(match.group(1))
                is_default = True

            # Named exports: export function X, export const X, export class X
            for pattern in (
                _TS_EXPORT_FUNCTION_RE,
                _TS_EXPORT_CONST_RE,
                _TS_EXPORT_INTERFACE_RE,
            ):
                for match in pattern.finditer(content):
                    exports.add(match.group(1))

            if not exports:
                # Fallback: use the filename as the component name
                exports = {basename_no_ext}

            # Build import path: @/components/[subdir/]Name (without extension)
            rel_to_components = os.path.relpath(fpath, components_dir)
            # Remove extension for import path
            import_rel = os.path.splitext(rel_to_components)[0]
            import_path = f"@/components/{import_rel}"

            components.append({
                "import_path": import_path,
                "exports": sorted(exports),
                "is_default": is_default,
            })

        if len(components) >= max_components:
            break

    return sorted(components, key=lambda c: c["import_path"])



# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def scan_project_state(project_dir: str) -> Dict[str, List[str]]:
    """Scan a project directory and extract its current state.

    This is the equivalent of Roo-Code's getEnvironmentDetails() — it
    captures everything an agent needs to know about the project BEFORE
    it starts writing code.

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        Dict with keys:
        - prisma_models: list of model/enum names from schema.prisma
        - exported_types: list of exported TS type/interface names
        - existing_files: list of relative paths to source files
        - api_routes: list of relative paths to API route files
        - config_files: list of relative paths to config files
    """
    if not os.path.isdir(project_dir):
        return {
            "prisma_models": [],
            "exported_types": [],
            "existing_files": [],
            "api_routes": [],
            "config_files": [],
        }

    # Detect project layout once, pass to all helpers
    layout = detect_layout(project_dir)

    # 1. Prisma models
    schema_path = os.path.join(project_dir, "prisma", "schema.prisma")
    prisma_models = _extract_prisma_names(schema_path)

    # 2. TypeScript exports
    types_file = _find_types_file(project_dir, layout=layout)
    exported_types = _extract_ts_exports(types_file) if types_file else []

    # 3. File tree
    tree = _scan_file_tree(project_dir, layout=layout)

    # 4. Design artifacts (F6-a, RCA-15)
    # design-tokens.json
    design_tokens = None
    tokens_path = _planning_path(project_dir, "design_tokens")
    if os.path.isfile(tokens_path):
        try:
            import json
            with open(tokens_path, "r", encoding="utf-8") as f:
                design_tokens = json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            pass

    # docs/design-mockups/*.png
    mockup_paths: List[str] = []
    mockups_dir = os.path.join(project_dir, "docs", "design-mockups")
    if os.path.isdir(mockups_dir):
        for fname in sorted(os.listdir(mockups_dir)):
            if fname.lower().endswith(".png"):
                mockup_paths.append(fname)

    # component-spec.md
    component_spec = None
    spec_path = _planning_path(project_dir, "component_spec")
    if os.path.isfile(spec_path):
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                component_spec = f.read()
        except (IOError, OSError):
            pass

    # 5. Component exports (Fix 4, ITR-32 RC-E)
    component_exports = _extract_component_exports(project_dir)

    return {
        "prisma_models": prisma_models,
        "exported_types": exported_types,
        "existing_files": tree["existing_files"],
        "api_routes": tree["api_routes"],
        "config_files": tree["config_files"],
        "prisma_definitions": extract_prisma_definitions(schema_path),
        "ts_definitions": _collect_ts_definitions(project_dir, layout=layout),
        "design_tokens": design_tokens,
        "mockup_paths": mockup_paths,
        "component_spec": component_spec,
        "component_exports": component_exports,
    }



def format_codebase_manifest(state: Dict[str, List[str]]) -> str:
    """Format scanned project state into a manifest for agent injection.

    Mirrors Roo-Code's <environment_details> block — a structured,
    machine-readable summary that gives the agent full awareness of
    the codebase before it writes a single line of code.

    Args:
        state: Output from scan_project_state()

    Returns:
        Formatted manifest string. Empty string if project has no
        meaningful content to report.
    """
    has_types = bool(state["prisma_models"] or state["exported_types"])
    has_files = bool(state["existing_files"])

    if not has_types and not has_files:
        return ""

    sections: List[str] = []

    # ── Type Contract (THE critical section) ──
    if has_types:
        type_lines = ["## Type Contract — MANDATORY"]
        type_lines.append(
            "These are the CANONICAL type names for this project. "
            "You MUST use these EXACT names when importing from `@/types`. "
            "Do NOT invent new type names. Do NOT guess."
        )

        if state["exported_types"]:
            type_lines.append("")
            type_lines.append("### Exported Types (available via `import { ... } from '@/types'`)")
            for t in state["exported_types"]:
                type_lines.append(f"- `{t}`")

        if state["prisma_models"]:
            type_lines.append("")
            type_lines.append("### Prisma Models (from `prisma/schema.prisma`)")
            for m in state["prisma_models"]:
                type_lines.append(f"- `{m}`")

            # Flag orphans — models without TS types
            exported_set = set(state["exported_types"])
            orphans = [m for m in state["prisma_models"] if m not in exported_set]
            if orphans:
                type_lines.append("")
                type_lines.append(
                    "⚠️ **Missing TypeScript types**: The following Prisma models "
                    "do NOT have corresponding exports in `@/types`. If you need "
                    "these types, add them to `src/types/index.ts`:"
                )
                for o in orphans:
                    type_lines.append(f"- `{o}` — needs `export interface {o} {{ ... }}`")

        sections.append("\n".join(type_lines))

    # ── Shared Type Definitions (Fix 6 — canonical shapes) ──
    has_defs = bool(state.get("prisma_definitions") or state.get("ts_definitions"))
    if has_defs:
        def_lines = ["## Shared Type Definitions — CANONICAL (use these EXACT shapes)"]
        def_lines.append(
            "The following are the full type definitions from this project. "
            "When referencing these types, use the EXACT field names, types, and "
            "values shown below. Do NOT create alternative definitions."
        )

        # Prisma definitions
        prisma_defs = state.get("prisma_definitions", {})
        if prisma_defs:
            def_lines.append("")
            def_lines.append("### Prisma Schema (from `prisma/schema.prisma`)")
            def_lines.append("```prisma")
            for name in sorted(prisma_defs.keys()):
                def_lines.append(prisma_defs[name])
                def_lines.append("")
            def_lines.append("```")

        # TypeScript definitions
        ts_defs = state.get("ts_definitions", {})
        if ts_defs:
            def_lines.append("")
            def_lines.append("### TypeScript Types (from type definition files)")
            def_lines.append("```typescript")
            for name in sorted(ts_defs.keys()):
                def_lines.append(ts_defs[name])
                def_lines.append("")
            def_lines.append("```")

        sections.append("\n".join(def_lines))

    # ── Existing Files ──
    if has_files:
        file_lines = ["## Existing Project Files"]
        file_lines.append(
            "These files ALREADY EXIST. Do NOT recreate them unless explicitly asked. "
            "Read them first if you need to modify them."
        )
        file_lines.append("")

        # Group by directory for readability
        dir_groups: Dict[str, List[str]] = {}
        for f in state["existing_files"]:
            dirname = os.path.dirname(f)
            if dirname not in dir_groups:
                dir_groups[dirname] = []
            dir_groups[dirname].append(os.path.basename(f))

        for dirname in sorted(dir_groups.keys()):
            file_lines.append(f"### `{dirname}/`")
            for fname in sorted(dir_groups[dirname]):
                file_lines.append(f"- {fname}")

        sections.append("\n".join(file_lines))

    # ── Component Import Guide (Fix 4, ITR-32 RC-E) ──
    component_exports = state.get("component_exports", [])
    if component_exports:
        guide_lines = ["## Component Import Guide — MANDATORY"]
        guide_lines.append(
            "These components ALREADY EXIST. You MUST import these components "
            "instead of recreating them. Use the exact import paths shown below."
        )
        guide_lines.append("")

        for comp in component_exports:
            import_path = comp["import_path"]
            exports = comp["exports"]
            is_default = comp.get("is_default", False)

            if is_default and len(exports) == 1:
                # Default export: import Hero from '@/components/Hero'
                guide_lines.append(
                    f"- `import {exports[0]} from '{import_path}'`"
                )
            elif exports:
                # Named exports: import {{ Navbar, NavbarMobile }} from '@/components/Navbar'
                names = ", ".join(exports)
                guide_lines.append(
                    f"- `import {{ {names} }} from '{import_path}'`"
                )

        sections.append("\n".join(guide_lines))

    # ── API Routes ──
    if state["api_routes"]:
        route_lines = ["## Existing API Routes"]
        route_lines.append("These API endpoints are already implemented:")
        route_lines.append("")
        for r in state["api_routes"]:
            # Extract the route path from the file path
            # e.g., src/app/api/businesses/route.ts → /api/businesses
            route_path = r
            # Strip source directory prefixes (e.g., 'src/app', 'app')
            for prefix in ("src/app", "app"):
                if route_path.startswith(prefix):
                    route_path = route_path[len(prefix):]
                    break
            # Strip route file suffixes
            for suffix in ("/route.ts", "/route.js", "/route.tsx"):
                if route_path.endswith(suffix):
                    route_path = route_path[:-len(suffix)]
                    break
            route_lines.append(f"- `{route_path}` → `{r}`")
        sections.append("\n".join(route_lines))

    # ── Config Files ──
    if state["config_files"]:
        config_lines = ["## Configuration Files"]
        config_lines.append("These config files exist — do NOT create duplicates:")
        config_lines.append("")
        for c in state["config_files"]:
            config_lines.append(f"- `{c}`")
        sections.append("\n".join(config_lines))

    return "\n\n".join(sections)


# ──────────────────────────────────────────────────────────────────────
# Blueprint Propagation — inject architect_plan.json specs
# ──────────────────────────────────────────────────────────────────────

def _extract_blueprint_specs(project_dir: str) -> str:
    """Extract engineering specs from architect_plan.json for injection.

    This is the ROOT FIX for stub/mock code: developer agents receive
    exact response shapes, data models, and component bindings from the
    architect's blueprint instead of guessing.

    Returns a formatted markdown section, or empty string if no plan exists
    or has no spec sections.
    """
    import json
    plan_path = _planning_path(project_dir, "architect_plan")
    if not os.path.isfile(plan_path):
        return ""

    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return ""

    if not isinstance(plan, dict):
        return ""

    sections: List[str] = []

    # ── API Contracts ──
    contracts = plan.get("api_contracts", {})
    if contracts:
        lines = ["### API Contracts (from architect_plan.json — IMPLEMENT THESE EXACT SHAPES)"]
        lines.append("")
        for route, methods in contracts.items():
            if not isinstance(methods, dict):
                continue
            for method, spec in methods.items():
                if not isinstance(spec, dict):
                    continue
                shape = spec.get("response_shape", {})
                body = spec.get("request_body", {})
                lines.append(f"**{method} `{route}`**:")
                if body:
                    lines.append(f"  - Request body: `{json.dumps(body)}`")
                if shape:
                    lines.append(f"  - Response shape: `{json.dumps(shape)}`")
                lines.append("")
        sections.append("\n".join(lines))

    # ── Data Models ──
    models = plan.get("data_models", {})
    if models:
        lines = ["### Data Models (from architect_plan.json — USE THESE EXACT FIELDS)"]
        lines.append("")
        for model_name, fields in models.items():
            if isinstance(fields, dict):
                field_list = ", ".join(f"{k}: {v}" for k, v in fields.items())
                lines.append(f"- **{model_name}**: `{{ {field_list} }}`")
            else:
                lines.append(f"- **{model_name}**: {fields}")
        lines.append("")
        sections.append("\n".join(lines))

    # ── Component Bindings ──
    bindings = plan.get("component_bindings", {})
    if bindings:
        lines = ["### Component → API Bindings (from architect_plan.json)"]
        lines.append("Each page MUST fetch from its bound API — do NOT use mock data.")
        lines.append("")
        for route, binding in bindings.items():
            if isinstance(binding, dict):
                data_src = binding.get("data_source", "unknown")
                state = binding.get("state_pattern", "")
                renders = binding.get("renders", "")
                lines.append(f"- **`{route}`**: fetch from `{data_src}`")
                if state:
                    lines.append(f"  - State: `{state}`")
                if renders:
                    lines.append(f"  - Renders: {renders}")
            lines.append("")
        sections.append("\n".join(lines))

    # ── Service Libraries ──
    service_libs = plan.get("service_libs", {})
    if service_libs:
        lines = ["### Service Libraries (from architect_plan.json)"]
        lines.append("")
        for lib_path, lib_spec in service_libs.items():
            if isinstance(lib_spec, dict):
                exports = lib_spec.get("exports", [])
                used_by = lib_spec.get("used_by", [])
                lines.append(f"- **`{lib_path}`**: exports `{exports}`, used by `{used_by}`")
        lines.append("")
        sections.append("\n".join(lines))

    if not sections:
        return ""

    header = (
        "## Architecture Blueprint (from architect_plan.json — MANDATORY)\n\n"
        "The architect has defined exact specifications below. You MUST implement "
        "these contracts as written. Do NOT guess response shapes or data models — "
        "they are defined here.\n"
    )
    return header + "\n".join(sections)


def inject_codebase_state(project_dir: str, message: str) -> str:
    """Scan project state and inject manifest into a delegation message.

    This is the main entry point, called from call_subordinate.py
    right before dispatching the subordinate agent. It mirrors
    Roo-Code's pattern of appending <environment_details> to every
    user message.

    Now also injects architect blueprint specs (api_contracts, data_models,
    component_bindings, service_libs) when architect_plan.json exists.

    Args:
        project_dir: Absolute path to the project directory.
        message: The original delegation message.

    Returns:
        Message with codebase state manifest prepended.
        If project has no meaningful state, returns message unchanged.
    """
    if not project_dir or not os.path.isdir(project_dir):
        return message

    try:
        state = scan_project_state(project_dir)
        manifest = format_codebase_manifest(state)
    except Exception as e:
        logger.warning(f"Failed to scan project state: {e}")
        return message

    # ── Blueprint specs (the key upstream quality fix) ──
    blueprint_section = ""
    try:
        blueprint_section = _extract_blueprint_specs(project_dir)
        if blueprint_section:
            logger.info(
                f"Injected architect blueprint specs into delegation message"
            )
    except Exception as e:
        logger.warning(f"Blueprint extraction failed (non-fatal): {e}")

    if not manifest and not blueprint_section:
        return message

    parts = [
        "## Codebase State (Auto-Injected — READ THIS FIRST)\n\n"
        "The following is a snapshot of the project's current state. "
        "This information is auto-generated to ensure you have full "
        "awareness of existing files, types, and configurations.\n"
    ]

    if blueprint_section:
        parts.append(blueprint_section)

    if manifest:
        parts.append(manifest)

    parts.append("---\n\n")
    parts.append(message)

    injected = "\n\n".join(parts)

    logger.info(
        f"Injected codebase state manifest into delegation message "
        f"(types={len(state['exported_types'])}, "
        f"models={len(state['prisma_models'])}, "
        f"files={len(state['existing_files'])}, "
        f"routes={len(state['api_routes'])}, "
        f"has_blueprint={bool(blueprint_section)})"
    )

    return injected

