"""
Iteration Warm-Start Component Registry — U-8

Scans an existing project's src/ directory to build a registry of all
custom components, lib utilities, and API routes built in previous
iterations. This registry is injected into delegation messages so
subsequent iterations REUSE existing components instead of rebuilding.

Root Cause (ISS-06): node_project_scaffold.py creates a fresh Next.js
scaffold every iteration, losing 12+ custom components built in staging.
This module provides the scanning infrastructure to preserve them.

Usage:
    registry = build_component_registry(project_dir)
    markdown = format_registry_for_delegation(registry)
    # Inject `markdown` into delegation message
"""

import logging
import os
import re
from typing import Any, Dict, List

logger = logging.getLogger("agix.iteration_warm_start")

# ─── Scaffold Default Files to Exclude ────────────────────────────────────
# These are created by create-next-app and should NOT be treated as
# custom components worth preserving across iterations.
_SCAFFOLD_DEFAULTS = frozenset({
    "layout.tsx", "layout.ts", "layout.jsx", "layout.js",
    "page.tsx", "page.ts", "page.jsx", "page.js",
    "globals.css", "global.css",
    "loading.tsx", "loading.ts", "loading.jsx", "loading.js",
    "error.tsx", "error.ts", "error.jsx", "error.js",
    "not-found.tsx", "not-found.ts", "not-found.jsx", "not-found.js",
    "favicon.ico",
    "opengraph-image.png", "opengraph-image.jpg",
    "sitemap.ts", "sitemap.js",
    "robots.ts", "robots.js",
    "manifest.ts", "manifest.js",
})

# File extensions we scan for
_SCANNABLE_EXTENSIONS = frozenset({".tsx", ".ts", ".jsx", ".js"})

# Regex patterns to extract export names from TypeScript/JavaScript
_EXPORT_DEFAULT_RE = re.compile(
    r"export\s+default\s+(?:function|class|const|let|var)?\s*(\w+)",
)
_EXPORT_NAMED_RE = re.compile(
    r"export\s+(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)",
)


def build_component_registry(project_dir: str) -> List[Dict[str, Any]]:
    """Scan project src/ directory and build a component registry.

    Scans three key directories:
      - src/components/ — React components
      - src/lib/ — utility modules and SDK integrations
      - src/app/api/ — API route handlers

    Each entry captures:
      - name: Component/module name (filename without extension)
      - path: Relative path from project root
      - exports: List of exported symbol names
      - line_count: Number of lines in the file
      - is_component: True if in src/components/
      - is_lib: True if in src/lib/
      - is_route: True if in src/app/api/

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        List of registry entry dicts, sorted by path. Empty if no
        scannable files found.
    """
    src_dir = os.path.join(project_dir, "src")
    if not os.path.isdir(src_dir):
        return []

    registry: List[Dict[str, Any]] = []

    # Define scan targets with their classification flags
    scan_targets = [
        {
            "dir": os.path.join(src_dir, "components"),
            "is_component": True,
            "is_lib": False,
            "is_route": False,
        },
        {
            "dir": os.path.join(src_dir, "lib"),
            "is_component": False,
            "is_lib": True,
            "is_route": False,
        },
        {
            "dir": os.path.join(src_dir, "app", "api"),
            "is_component": False,
            "is_lib": False,
            "is_route": True,
        },
    ]

    for target in scan_targets:
        target_dir = target["dir"]
        if not os.path.isdir(target_dir):
            continue

        for root, _dirs, files in os.walk(target_dir):
            for filename in files:
                # Skip non-scannable extensions
                _name, ext = os.path.splitext(filename)
                if ext not in _SCANNABLE_EXTENSIONS:
                    continue

                # Skip scaffold default files
                if filename in _SCAFFOLD_DEFAULTS:
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, project_dir)

                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except (IOError, OSError) as e:
                    logger.warning(
                        f"[WARM-START] Failed to read {rel_path}: {e}"
                    )
                    continue

                exports = _extract_exports(content)
                line_count = len(content.splitlines())
                name = _name  # filename without extension

                # For route files, use parent directory name as context
                if target["is_route"] and filename.startswith("route"):
                    parent_dir = os.path.basename(root)
                    name = f"route_{parent_dir}"

                registry.append({
                    "name": name,
                    "path": rel_path,
                    "exports": exports,
                    "line_count": line_count,
                    "is_component": target["is_component"],
                    "is_lib": target["is_lib"],
                    "is_route": target["is_route"],
                })

    # Sort by path for deterministic output
    registry.sort(key=lambda e: e["path"])

    if registry:
        logger.info(
            f"[WARM-START] Built registry with {len(registry)} entries "
            f"({sum(1 for e in registry if e['is_component'])} components, "
            f"{sum(1 for e in registry if e['is_lib'])} lib, "
            f"{sum(1 for e in registry if e['is_route'])} routes)"
        )

    return registry


def format_registry_for_delegation(registry: List[Dict[str, Any]]) -> str:
    """Format component registry as markdown for delegation message injection.

    Produces a markdown table suitable for embedding in a delegation
    message, so the subordinate agent knows which components already
    exist and should be reused.

    Args:
        registry: List of registry entry dicts from build_component_registry().

    Returns:
        Markdown string. Empty string if registry is empty.
    """
    if not registry:
        return ""

    lines = [
        "## 🔄 Existing Components (REUSE — do NOT rebuild)",
        "",
        "The following components were built in previous iterations. "
        "**IMPORT and REUSE them** — do NOT create duplicates.",
        "",
        "| Name | Path | Exports | Lines | Type |",
        "|------|------|---------|-------|------|",
    ]

    for entry in registry:
        name = entry["name"]
        path = entry["path"]
        exports = ", ".join(entry["exports"][:5])  # Cap at 5 for readability
        if len(entry["exports"]) > 5:
            exports += f" (+{len(entry['exports']) - 5} more)"
        line_count = entry["line_count"]

        if entry["is_component"]:
            entry_type = "Component"
        elif entry["is_lib"]:
            entry_type = "Lib"
        elif entry["is_route"]:
            entry_type = "API Route"
        else:
            entry_type = "Other"

        lines.append(f"| {name} | `{path}` | {exports} | {line_count} | {entry_type} |")

    lines.append("")
    return "\n".join(lines)


def _extract_exports(content: str) -> List[str]:
    """Extract exported symbol names from TypeScript/JavaScript source.

    Finds both default and named exports. Deduplicates results.

    Args:
        content: File content string.

    Returns:
        List of export names (deduplicated, sorted).
    """
    exports = set()

    for match in _EXPORT_DEFAULT_RE.finditer(content):
        exports.add(match.group(1))

    for match in _EXPORT_NAMED_RE.finditer(content):
        exports.add(match.group(1))

    return sorted(exports)
