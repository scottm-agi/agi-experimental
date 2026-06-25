"""
Navigation Map Markdown Builder.

Standalone function that renders scanned route/link data into a structured
markdown document. Extracted from BuildNavigationMap to keep testable without
pulling in the heavy Tool → Agent import chain.

Used by:
  - python/tools/build_navigation_map.py (the tool)
  - tests/test_build_navigation_map.py (unit tests)

RCA: MSR_Smoke_1777164282 Cluster #2 — _build_map_markdown was called but
never defined, causing AttributeError on every invocation.
"""

from __future__ import annotations
from typing import List, Dict, Any
from datetime import datetime


def build_map_markdown(
    project_dir: str,
    framework: str,
    frontend_routes: List[Dict[str, Any]],
    api_routes: List[Dict[str, Any]],
    nav_links: List[Dict[str, Any]],
) -> str:
    """Render scanned route data into a structured markdown navigation map.

    Args:
        project_dir: Absolute path to the project root.
        framework: Detected framework name (e.g., "nextjs", "vite", "unknown").
        frontend_routes: List of frontend page route dicts with path, file, type.
        api_routes: List of API route dicts with path, file, type, methods.
        nav_links: List of navigation link dicts with href, source_file, type.

    Returns:
        A complete markdown document string.
    """
    lines: list[str] = []

    # ── Header ──
    project_name = project_dir.rstrip("/").split("/")[-1] if project_dir else "project"
    lines.append(f"# Navigation Map — {project_name}")
    lines.append("")
    lines.append(f"**Framework**: `{framework}`  ")
    lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
    lines.append(f"**Frontend Routes**: {len(frontend_routes)}  ")
    lines.append(f"**API Routes**: {len(api_routes)}  ")
    lines.append(f"**Navigation Links**: {len(nav_links)}")
    lines.append("")

    # ── Frontend Routes ──
    lines.append("## Frontend Routes")
    lines.append("")
    if frontend_routes:
        lines.append("| Route | File | Type |")
        lines.append("|-------|------|------|")
        for route in frontend_routes:
            path = route.get("path", "?")
            file_ = route.get("file", "?")
            type_ = route.get("type", "page")
            lines.append(f"| `{path}` | `{file_}` | {type_} |")
    else:
        lines.append("_No frontend routes discovered._")
    lines.append("")

    # ── API Routes ──
    lines.append("## API Routes")
    lines.append("")
    if api_routes:
        lines.append("| Route | Methods | File |")
        lines.append("|-------|---------|------|")
        for route in api_routes:
            path = route.get("path", "?")
            methods = ", ".join(route.get("methods", ["GET"]))
            file_ = route.get("file", "?")
            lines.append(f"| `{path}` | {methods} | `{file_}` |")
    else:
        lines.append("_No API routes discovered._")
    lines.append("")

    # ── Navigation Links ──
    lines.append("## Navigation Links")
    lines.append("")
    if nav_links:
        lines.append("| Href | Source File | Type |")
        lines.append("|------|------------|------|")
        for link in nav_links[:50]:  # Cap at 50 for readability
            href = link.get("href", "?")
            source = link.get("source_file", "?")
            type_ = link.get("type", "link")
            lines.append(f"| `{href}` | `{source}` | {type_} |")
        if len(nav_links) > 50:
            lines.append(f"| ... | _({len(nav_links) - 50} more)_ | |")
    else:
        lines.append("_No navigation links discovered._")
    lines.append("")

    # ── Reachability Summary ──
    lines.append("## Reachability Summary")
    lines.append("")
    linked_paths = {link.get("href", "") for link in nav_links}
    route_paths = {route.get("path", "") for route in frontend_routes}
    unreachable = route_paths - linked_paths - {"/"}  # Root is always reachable
    if unreachable:
        lines.append(f"⚠️ **{len(unreachable)} routes have no navigation links pointing to them:**")
        lines.append("")
        for path in sorted(unreachable):
            lines.append(f"- `{path}`")
    else:
        lines.append("✅ All frontend routes are reachable via navigation links.")
    lines.append("")

    return "\n".join(lines)
