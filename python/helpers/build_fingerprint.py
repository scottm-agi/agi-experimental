"""
Build Fingerprint — track source file hashes to skip redundant build cycles.

RCA-335 ISS-1: In iteration 335, the orchestrator triggered 272+ npm run build
cycles because the gate only checks for .next/ existence, not whether sources
have actually changed since the last build.

This module provides:
- compute_source_fingerprint(project_dir): MD5 hash of all source files
- save_build_fingerprint(project_dir): Save current fingerprint after build
- should_skip_build(project_dir): True if sources unchanged since last build

The fingerprint is stored in .agix.proj/build_fingerprint.json in the project.

Usage:
    from python.helpers.build_fingerprint import should_skip_build, save_build_fingerprint

    if should_skip_build(project_dir):
        logger.info("Sources unchanged — skipping build")
    else:
        run_build()
        if build_succeeded:
            save_build_fingerprint(project_dir)
"""

import json
import logging
import os
from typing import Optional

from python.helpers.hashing import content_hash
from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS

logger = logging.getLogger("agix.build_fingerprint")

# DUP-3: Uses shared DEFAULT_PROJECT_SKIP_DIRS + fingerprint-specific extras.
_EXCLUDE_DIRS = DEFAULT_PROJECT_SKIP_DIRS | frozenset({".nyc_output"})

# File extensions to include in fingerprint
_SOURCE_EXTENSIONS = frozenset({
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".css", ".scss", ".less", ".sass",
    ".json", ".md", ".mdx",
    ".html", ".htm",
    ".prisma", ".graphql", ".gql",
    ".env", ".env.example", ".env.local",
})

# Config files at project root always included
_ROOT_CONFIG_FILES = {
    "next.config.js", "next.config.ts", "next.config.mjs",
    "tailwind.config.js", "tailwind.config.ts",
    "postcss.config.js", "postcss.config.mjs",
    "tsconfig.json", "package.json",
}

_FINGERPRINT_DIR = ".agix.proj"
_FINGERPRINT_FILE = "build_fingerprint.json"


def compute_source_fingerprint(project_dir: str) -> str:
    """Compute a deterministic MD5 fingerprint of all source files.

    Walks the project directory, hashes all source files (sorted by path
    for determinism), and returns a hex digest.

    Uses the universal content_hash (MD5) from python.helpers.hashing —
    this is a non-security use case (change detection), not crypto.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        MD5 hex digest string (32 chars) representing the current source state.
    """
    file_hashes = []

    for root, dirs, files in os.walk(project_dir):
        # Filter excluded directories
        dirs[:] = sorted(d for d in dirs if d not in _EXCLUDE_DIRS)

        rel_root = os.path.relpath(root, project_dir)

        for fname in sorted(files):
            # Include if extension matches OR it's a root config file
            _, ext = os.path.splitext(fname)
            is_root = rel_root == "."
            if ext.lower() not in _SOURCE_EXTENSIONS and not (is_root and fname in _ROOT_CONFIG_FILES):
                continue

            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "rb") as f:
                    raw = f.read()
                rel_path = os.path.relpath(fpath, project_dir)
                file_hash = content_hash(raw.decode("utf-8", errors="replace"))
                file_hashes.append(f"{rel_path}:{file_hash}")
            except (IOError, OSError):
                continue

    # Sort for determinism and hash the combined result
    combined = "\n".join(sorted(file_hashes))
    return content_hash(combined)


def save_build_fingerprint(project_dir: str) -> None:
    """Save the current source fingerprint after a successful build.

    Stores the fingerprint in .agix.proj/build_fingerprint.json.

    Args:
        project_dir: Absolute path to the project root.
    """
    fingerprint = compute_source_fingerprint(project_dir)
    fp_dir = os.path.join(project_dir, _FINGERPRINT_DIR)
    os.makedirs(fp_dir, exist_ok=True)

    fp_path = os.path.join(fp_dir, _FINGERPRINT_FILE)
    try:
        with open(fp_path, "w") as f:
            json.dump({
                "fingerprint": fingerprint,
                "timestamp": __import__("time").time(),
            }, f, indent=2)
        logger.info(f"[BUILD FP] Saved build fingerprint: {fingerprint[:16]}...")
    except (IOError, OSError) as e:
        logger.warning(f"[BUILD FP] Failed to save fingerprint: {e}")


def should_skip_build(project_dir: str) -> bool:
    """Check if a build can be skipped because sources haven't changed.

    Compares the current source fingerprint against the saved one from
    the last successful build.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        True if sources are unchanged since last build (safe to skip).
        False if sources changed or no previous build fingerprint exists.
    """
    fp_path = os.path.join(project_dir, _FINGERPRINT_DIR, _FINGERPRINT_FILE)

    if not os.path.isfile(fp_path):
        return False  # No previous build — must build

    try:
        with open(fp_path, "r") as f:
            data = json.load(f)
        saved_fingerprint = data.get("fingerprint", "")
    except (IOError, OSError, json.JSONDecodeError):
        return False  # Can't read — must build

    if not saved_fingerprint:
        return False

    current_fingerprint = compute_source_fingerprint(project_dir)
    is_same = current_fingerprint == saved_fingerprint

    if is_same:
        logger.info(
            f"[BUILD FP] Sources unchanged ({current_fingerprint[:16]}...) — "
            f"build can be skipped"
        )
    else:
        logger.info(
            f"[BUILD FP] Sources changed: "
            f"saved={saved_fingerprint[:16]}... current={current_fingerprint[:16]}..."
        )

    return is_same
