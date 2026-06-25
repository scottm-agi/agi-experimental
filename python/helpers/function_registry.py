"""
Function Registry (ADR-008) — Scans project source files for exported
symbols to prevent cross-wave duplication.

Root cause (Iteration 144): calculateAuditScore was duplicated verbatim
in discovery.ts and audit.ts because Wave 2 had no awareness of symbols
created in Wave 1.

The registry provides:
1. Source scanning for exported functions/types/classes/interfaces
2. Symbol summary formatting for delegation message injection
3. Duplicate detection across files
"""
from __future__ import annotations
import os
import re
import logging
from typing import Dict, List, Optional, Set

from python.helpers.source_scanner import read_project_files, EXCLUDE_DIRS

logger = logging.getLogger("agix.function_registry")

# ── Export patterns for TypeScript/JavaScript ──
_EXPORT_PATTERNS = [
    # export function name(...)
    re.compile(r"export\s+(?:async\s+)?function\s+(\w+)"),
    # export default function name(...)
    re.compile(r"export\s+default\s+(?:async\s+)?function\s+(\w+)"),
    # export const name = ...
    re.compile(r"export\s+const\s+(\w+)\s*[=:]"),
    # export class name
    re.compile(r"export\s+(?:default\s+)?class\s+(\w+)"),
    # export type name = ...
    re.compile(r"export\s+type\s+(\w+)\s*[={]"),
    # export interface name
    re.compile(r"export\s+interface\s+(\w+)"),
    # export enum name
    re.compile(r"export\s+enum\s+(\w+)"),
]

# ── Source file extensions to scan ──
_SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

# ── Directories to skip ──
_SKIP_DIRS = {"node_modules", ".next", ".git", "dist", "build", ".cache", "__pycache__"}

# ── Common names to ignore in duplicate detection ──
_COMMON_NAMES = {"default", "index", "App", "Home", "Page", "Layout", "Loading", "Error"}


def scan_source_for_exports(project_root: str) -> Dict[str, List[str]]:
    """Scan project source files for exported symbols.

    Walks the project directory tree, reads each source file, and
    extracts exported function/class/type/interface/const names.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        Dict mapping relative file paths to lists of exported symbol names.
    """
    symbols: Dict[str, List[str]] = {}

    if not os.path.isdir(project_root):
        return symbols

    # OVL-3: Use centralized scanner instead of inline os.walk
    file_contents = read_project_files(
        project_root,
        extensions=_SOURCE_EXTENSIONS,
        skip_dirs=EXCLUDE_DIRS | _SKIP_DIRS,
    )

    for rel_path, content in file_contents.items():
        exports = _extract_exports(content)
        if exports:
            symbols[rel_path] = exports

    return symbols


def _extract_exports(content: str) -> List[str]:
    """Extract exported symbol names from file content."""
    names: List[str] = []
    seen: Set[str] = set()

    for pattern in _EXPORT_PATTERNS:
        for match in pattern.finditer(content):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)

    return names


def format_symbol_summary(symbols: Dict[str, List[str]]) -> Optional[str]:
    """Format the symbol registry into a delegation-ready text block.

    Produces a human-readable list of symbols with their file paths,
    suitable for injection into subordinate delegation messages.

    Args:
        symbols: Dict mapping file paths to lists of exported symbol names.

    Returns:
        Formatted string, or None if no symbols.
    """
    if not symbols:
        return None

    lines = []
    for file_path in sorted(symbols.keys()):
        names = symbols[file_path]
        basename = os.path.basename(file_path)
        for name in names:
            lines.append(f"- {name} → {file_path}")

    return "\n".join(lines)


def detect_duplicates(symbols: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Find symbols that are defined in multiple files.

    Ignores common names like 'default', 'index', 'App' etc.

    Args:
        symbols: Dict mapping file paths to lists of exported symbol names.

    Returns:
        Dict mapping duplicate symbol names to lists of file paths
        where they appear. Only includes symbols found in 2+ files.
    """
    # Build reverse index: symbol name → list of files
    name_to_files: Dict[str, List[str]] = {}

    for file_path, names in symbols.items():
        for name in names:
            if name in _COMMON_NAMES:
                continue
            if name not in name_to_files:
                name_to_files[name] = []
            name_to_files[name].append(file_path)

    # Filter to duplicates only
    return {
        name: files
        for name, files in name_to_files.items()
        if len(files) > 1
    }
