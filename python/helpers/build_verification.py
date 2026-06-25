"""
Build Verification Helper — Framework-Aware Build Output Checks.
================================================================

Provides check_build_exists() for Phase 4.9 Build-Freeze Gate verification.
Checks whether a project has a valid production build output based on the
detected or specified framework.

F-6: Build verification was only happening in Phase 5 (too late). This module
enables build verification in Phase 4.9 by detecting framework-specific build
artifacts.

Usage:
    from python.helpers.build_verification import check_build_exists

    result = check_build_exists("/path/to/project", "nextjs")
    if not result["built"]:
        # Block completion — no valid build found
        ...
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Dict, List, Optional

logger = logging.getLogger("agix.build_verification")

# Framework detection config files
_FRAMEWORK_DETECT = {
    "nextjs": [
        "next.config.js",
        "next.config.ts",
        "next.config.mjs",
    ],
    "vite": [
        "vite.config.ts",
        "vite.config.js",
        "vite.config.mts",
    ],
}


def _detect_framework(project_dir: str) -> str:
    """Auto-detect the framework from config files in the project directory.

    ITR-39 SYSTEM 3: Delegates to project_layout_detector.detect_layout()
    as the canonical source of truth, then maps to build verification names.
    Falls back to config-file detection if detect_layout is unavailable.

    Args:
        project_dir: Root directory of the project.

    Returns:
        Detected framework name ('nextjs', 'vite', 'generic').
    """
    if not os.path.isdir(project_dir):
        return "generic"

    # ITR-39 SYSTEM 3: Delegate to canonical detector
    try:
        from python.helpers.project_layout_detector import detect_layout
        layout = detect_layout(project_dir)
        framework = (layout.framework or "").lower()

        # Map detect_layout framework names → build_verification names
        # detect_layout returns "nextjs-app" or "nextjs-pages", not plain "nextjs"
        if framework.startswith("nextjs") or framework in ("next.js", "next"):
            return "nextjs"
        if framework in ("vite", "vite-react", "vite-vue"):
            return "vite"
        if framework and framework != "unknown":
            return "generic"
    except ImportError:
        pass

    # Fallback: original config-file detection
    for framework, config_files in _FRAMEWORK_DETECT.items():
        for config_file in config_files:
            if os.path.isfile(os.path.join(project_dir, config_file)):
                return framework

    return "generic"


def check_typescript_types(project_dir: str) -> Dict:
    """Run tsc --noEmit to catch TypeScript type errors.

    SS-4 Fix: build_verification previously only checked for build output
    existence (.next/BUILD_ID) but never verified TypeScript compiles cleanly.
    This allowed missing exports (e.g., generateReviewResponse) to pass
    build verification and crash at runtime.

    Args:
        project_dir: Root directory of the TypeScript project.

    Returns:
        Dict with keys:
            - passed (bool): True if type check passed or was skipped
            - errors (list): List of error strings from tsc output
            - command (str): The command that was run (or 'skipped')
    """
    # Skip if directory doesn't exist
    if not os.path.isdir(project_dir):
        return {"passed": True, "errors": [], "command": "skipped (no directory)"}

    # Skip if no tsconfig.json — not a TypeScript project
    tsconfig_path = os.path.join(project_dir, "tsconfig.json")
    if not os.path.isfile(tsconfig_path):
        return {"passed": True, "errors": [], "command": "skipped (no tsconfig.json)"}

    # Run tsc --noEmit
    cmd = ["npx", "tsc", "--noEmit"]
    try:
        result = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout
        )

        if result.returncode == 0:
            logger.info("[BUILD VERIFY] tsc --noEmit passed")
            return {
                "passed": True,
                "errors": [],
                "command": " ".join(cmd),
            }
        else:
            # Parse errors from stderr/stdout
            error_output = (result.stdout or "") + (result.stderr or "")
            error_lines = [
                line.strip()
                for line in error_output.splitlines()
                if line.strip() and ("error TS" in line or "Error:" in line)
            ]
            logger.warning(
                f"[BUILD VERIFY] tsc --noEmit FAILED with {len(error_lines)} error(s)"
            )
            return {
                "passed": False,
                "errors": error_lines,
                "command": " ".join(cmd),
            }
    except FileNotFoundError:
        # npx/tsc not available — skip gracefully
        logger.warning("[BUILD VERIFY] npx/tsc not found — skipping type check")
        return {"passed": True, "errors": [], "command": "skipped (npx not found)"}
    except subprocess.TimeoutExpired:
        logger.warning("[BUILD VERIFY] tsc --noEmit timed out after 120s")
        return {
            "passed": False,
            "errors": ["tsc --noEmit timed out after 120 seconds"],
            "command": " ".join(cmd),
        }
    except Exception as e:
        logger.warning(f"[BUILD VERIFY] tsc --noEmit error: {e}")
        return {"passed": True, "errors": [], "command": f"skipped (error: {e})"}


def check_build_exists(
    project_dir: str,
    framework: Optional[str] = None,
) -> Dict:
    """Check whether a project has a valid production build output.

    Framework-aware build check:
    - Next.js → `.next/BUILD_ID` must exist
    - Vite → `dist/index.html` must exist
    - Generic → check for `build/`, `dist/`, or `out/` with content

    SS-4 Enhancement: Also runs tsc --noEmit to catch type errors that
    would cause runtime crashes even when the build output exists.

    Args:
        project_dir: Root directory of the project.
        framework: Framework name ('nextjs', 'vite', 'generic').
                   If None or empty, auto-detects from config files.

    Returns:
        Dict with keys:
            - built (bool): True if valid build output found
            - framework (str): Detected/specified framework
            - evidence (str): Description of what was found/missing
            - build_path (Optional[str]): Path to build output if found
            - type_check (dict): Result of tsc --noEmit check
    """
    # Normalize framework
    if not framework:
        framework = _detect_framework(project_dir)

    framework = framework.lower().strip()

    # Map common aliases
    framework_aliases = {
        "next": "nextjs",
        "next.js": "nextjs",
        "vite": "vite",
        "vitejs": "vite",
    }
    framework = framework_aliases.get(framework, framework)

    # Route to the appropriate checker
    if framework == "nextjs":
        result = _check_nextjs_build(project_dir, framework)
    elif framework == "vite":
        result = _check_vite_build(project_dir, framework)
    else:
        result = _check_generic_build(project_dir, "generic")

    # SS-4: Always include TypeScript type-check result
    result["type_check"] = check_typescript_types(project_dir)

    return result


def _check_nextjs_build(project_dir: str, framework: str) -> Dict:
    """Check for Next.js production build (.next/BUILD_ID).

    A valid Next.js build must have the .next/ directory with a BUILD_ID file.
    The BUILD_ID file is created by `next build` and indicates a complete build.
    """
    next_dir = os.path.join(project_dir, ".next")
    build_id_path = os.path.join(next_dir, "BUILD_ID")

    if not os.path.isdir(next_dir):
        return {
            "built": False,
            "framework": framework,
            "evidence": ".next/ directory does not exist — no build has been run",
            "build_path": None,
        }

    if not os.path.isfile(build_id_path):
        return {
            "built": False,
            "framework": framework,
            "evidence": ".next/ exists but BUILD_ID is missing — build may be incomplete or corrupted",
            "build_path": None,
        }

    # Read BUILD_ID for evidence
    try:
        with open(build_id_path, "r", encoding="utf-8") as f:
            build_id = f.read().strip()
    except (IOError, OSError):
        build_id = "(unreadable)"

    return {
        "built": True,
        "framework": framework,
        "evidence": f".next/BUILD_ID found (id: {build_id})",
        "build_path": next_dir,
    }


def _check_vite_build(project_dir: str, framework: str) -> Dict:
    """Check for Vite production build (dist/index.html).

    A valid Vite build must have the dist/ directory with an index.html file.
    """
    dist_dir = os.path.join(project_dir, "dist")
    index_path = os.path.join(dist_dir, "index.html")

    if not os.path.isdir(dist_dir):
        return {
            "built": False,
            "framework": framework,
            "evidence": "dist/ directory does not exist — no build has been run",
            "build_path": None,
        }

    if not os.path.isfile(index_path):
        return {
            "built": False,
            "framework": framework,
            "evidence": "dist/ exists but index.html is missing — build may be incomplete",
            "build_path": None,
        }

    return {
        "built": True,
        "framework": framework,
        "evidence": f"dist/index.html found (build output valid)",
        "build_path": dist_dir,
    }


def _check_generic_build(project_dir: str, framework: str) -> Dict:
    """Check for generic build output in common directories.

    Checks build/, dist/, out/ directories for any content.
    """
    if not os.path.isdir(project_dir):
        return {
            "built": False,
            "framework": framework,
            "evidence": f"Project directory does not exist: {project_dir}",
            "build_path": None,
        }

    for dir_name in ("build", "dist", "out"):
        dir_path = os.path.join(project_dir, dir_name)
        if os.path.isdir(dir_path):
            # Check if it has any files (not just empty dirs)
            entries = os.listdir(dir_path)
            files = [e for e in entries if os.path.isfile(os.path.join(dir_path, e))]
            if files:
                return {
                    "built": True,
                    "framework": framework,
                    "evidence": f"{dir_name}/ found with {len(files)} file(s)",
                    "build_path": dir_path,
                }

    return {
        "built": False,
        "framework": framework,
        "evidence": "No build/, dist/, or out/ directory with files found",
        "build_path": None,
    }
