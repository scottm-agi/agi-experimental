"""
Build-pass advisory check for the orchestrator completion gate.

RCA-238 Fix B: Agents claim "all lint errors resolved" but the build
still fails. This check runs `npm run build` (or equivalent) and
reports pass/fail deterministically, preventing false completion claims.

This is an ADVISORY check — it doesn't block completion, but it surfaces
build failures as warnings so the orchestrator can re-delegate fixes.
"""
from __future__ import annotations

import functools
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from python.helpers.evidence_persistence import write_evidence
from python.helpers.project_layout_detector import detect_layout

logger = logging.getLogger("agix.build_pass_check")


def _testable(fn):
    """Decorator to make the function testable by exposing __wrapped__ with explicit args."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    wrapper.__wrapped__ = fn
    return wrapper


@_testable
def check_build_passes(
    project_dir: Optional[str] = None,
    exit_code: Optional[int] = None,
    output: Optional[str] = None,
) -> dict:
    """Check if the project build passes.
    
    Can be called in two modes:
    1. With project_dir: Runs `npm run build` and checks exit code.
    2. With exit_code/output directly: For testing (skip subprocess).
    
    Returns:
        dict with keys:
        - passed: bool — whether the build succeeded
        - reason: str — human-readable explanation
        - output: str — build output (truncated)
    """
    if exit_code is None and project_dir:
        # Actually run the build
        pkg_json = Path(project_dir) / "package.json"
        if not pkg_json.exists():
            return {
                "passed": True,
                "reason": "No package.json found — skipping build check",
                "output": "",
            }
        
        # Detect project layout for build command
        layout = detect_layout(project_dir)
        build_cmd = layout.build_command.split() if layout.build_command else ["npm", "run", "build"]

        try:
            result = subprocess.run(
                build_cmd,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=120,
                env={
                    **dict(__import__("os").environ),
                    "NODE_OPTIONS": "--max-old-space-size=4096",
                    "CI": "true",  # Treat warnings as non-blocking
                },
            )
            exit_code = result.returncode
            output = (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "reason": "Build timed out after 120 seconds",
                "output": "",
            }
        except Exception as e:
            return {
                "passed": False,
                "reason": f"Build failed to start: {e}",
                "output": "",
            }
    
    if exit_code == 0:
        return {
            "passed": True,
            "reason": "Build completed successfully",
            "output": (output or "")[-500:],  # Last 500 chars
        }
    else:
        # Extract the most relevant error lines
        error_lines = []
        for line in (output or "").split("\n"):
            if "error" in line.lower() or "failed" in line.lower():
                error_lines.append(line.strip())
        
        error_summary = "\n".join(error_lines[-5:]) if error_lines else (output or "")[-300:]
        
        return {
            "passed": False,
            "reason": f"Build failed (exit code {exit_code}): {error_summary[:200]}",
            "output": (output or "")[-500:],
        }


def check_build_passes_with_evidence(
    project_dir: str,
    exit_code: Optional[int] = None,
    output: Optional[str] = None,
) -> dict:
    """Wrapper that runs check_build_passes and persists evidence."""
    result = check_build_passes(project_dir=project_dir, exit_code=exit_code, output=output)
    if project_dir:
        write_evidence(project_dir, "build_evidence", {
            "passed": result["passed"],
            "reason": result["reason"][:300],
            "error_lines": [l.strip() for l in result.get("output", "").split("\n") if "error" in l.lower()][:5],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return result
