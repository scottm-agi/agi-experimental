"""
Content Regression Guard — prevents write_to_file from silently destroying
file content due to LLM output truncation.

When an LLM "improves" a large file using write_to_file, it must regenerate
the entire file in its output tokens. Under token pressure, it drops sections
(typically from the bottom), causing silent content loss. This guard detects
when the new content is significantly shorter than the existing file and
blocks the write with actionable guidance.

See: docs/rca/rca_iteration15_content_regression_overwrite.md
"""
from __future__ import annotations
import os
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from python.helpers.evidence_persistence import write_evidence

logger = logging.getLogger("agix.content_regression_guard")

# ── Configuration ──
REGRESSION_THRESHOLD = 0.30  # Block if new content is >30% shorter
MIN_LINES_TO_GUARD = 20     # Don't guard files under this line count

# ── RC-2: Per-file force bypass rate limiting ──
# ITR-35: Agents bypassed the guard 7 times using overwrite_force=True.
# Now limited to MAX_FORCE_BYPASSES_PER_FILE per file per session.
_force_bypass_counts: dict[str, int] = {}
MAX_FORCE_BYPASSES_PER_FILE = 1


def check_content_regression(
    abs_path: str,
    new_content: str,
    force: bool = False,
) -> str | None:
    """Check if writing new_content would cause content regression.

    Args:
        abs_path: Absolute path to the target file.
        new_content: The content that would be written.
        force: If True, bypass the guard entirely.

    Returns:
        None if the write is safe (or file doesn't exist).
        A warning message string if content regression is detected.
    """
    # RC-2: Rate-limited force bypass (ITR-35 fix)
    if force:
        file_key = os.path.basename(abs_path)
        count = _force_bypass_counts.get(file_key, 0)
        if count >= MAX_FORCE_BYPASSES_PER_FILE:
            logger.warning(
                f"[CONTENT REGRESSION GUARD] HARD BLOCK: overwrite_force already used "
                f"{count} time(s) on {file_key}. No more force bypasses allowed."
            )
            # Fall through to normal guard check instead of returning None
        else:
            _force_bypass_counts[file_key] = count + 1
            logger.info(
                f"[CONTENT REGRESSION GUARD] Force bypass #{count + 1} for {file_key}"
            )
            return None

    # No regression possible on new files
    if not os.path.exists(abs_path):
        return None

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            existing_content = f.read()
    except Exception:
        return None  # Can't read existing file — allow write

    existing_lines = existing_content.count("\n") + 1
    new_lines = new_content.count("\n") + 1

    # Small files are exempt — truncation risk is low
    if existing_lines < MIN_LINES_TO_GUARD:
        return None

    # Calculate content loss ratio
    if existing_lines == 0:
        return None

    retention_ratio = new_lines / existing_lines

    if retention_ratio < (1.0 - REGRESSION_THRESHOLD):
        loss_pct = int((1.0 - retention_ratio) * 100)
        basename = os.path.basename(abs_path)

        logger.warning(
            f"[CONTENT REGRESSION GUARD] Blocked overwrite of {basename}: "
            f"{existing_lines} → {new_lines} lines ({loss_pct}% content loss)"
        )

        return (
            f"⚠️ CONTENT REGRESSION GUARD: You are about to overwrite "
            f"'{basename}' ({existing_lines} lines) with only {new_lines} lines "
            f"({loss_pct}% content loss). "
            f"This almost always indicates unintentional truncation — your output "
            f"likely dropped sections from the original file due to token limits. "
            f"Use `replace_in_file` with targeted replacements to modify specific "
            f"sections without losing the rest. Full file rewrites that lose >30% content are blocked."
        )

    return None


def reset_force_bypass_counts():
    """Reset all per-file force bypass counters. Call at session start."""
    global _force_bypass_counts
    _force_bypass_counts = {}


# ──────────────────────────────────────────────────────────────────────
# L2: Heuristic refactoring-vs-truncation disambiguation
# ──────────────────────────────────────────────────────────────────────

# Patterns that indicate structural code elements
_IMPORT_PATTERN = re.compile(r"^\s*(?:import|from|require|const\s+\w+\s*=\s*require)", re.MULTILINE)
_FUNCTION_PATTERN = re.compile(r"(?:function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\(|\w+\s*=>)|def\s+\w+|export\s+(?:default\s+)?(?:function|class))", re.MULTILINE)
_EXPORT_PATTERN = re.compile(r"^\s*export\s+", re.MULTILINE)


def score_regression_confidence(
    existing_content: str, new_content: str, file_path: str,
) -> Dict[str, Any]:
    """Layer 2: Disambiguate intentional refactoring from LLM truncation.

    L1 catches line count regression. L2 determines whether
    the reduction is intentional (refactoring, cleanup) or accidental
    (LLM output truncation).

    Signals:
    1. Import preservation — truncation drops imports; refactoring preserves them
    2. Function count — truncation randomly loses functions; refactoring consolidates
    3. Export preservation — API surfaces should be preserved
    4. Tail abruptness — truncated output often ends mid-statement

    Args:
        existing_content: Current file content.
        new_content: Proposed replacement content.
        file_path: File path for context.

    Returns:
        dict with:
            is_truncation: bool (True = likely LLM truncation)
            confidence: float 0.0-1.0
            reasoning: str
    """
    score = 0.5  # Start neutral
    reasons = []

    # Signal 1: Import preservation
    old_imports = len(_IMPORT_PATTERN.findall(existing_content))
    new_imports = len(_IMPORT_PATTERN.findall(new_content))
    if old_imports > 0:
        import_ratio = new_imports / old_imports
        if import_ratio < 0.5:
            score += 0.25
            reasons.append(f"Import loss: {old_imports} → {new_imports} ({int((1-import_ratio)*100)}% lost)")
        elif import_ratio >= 0.9:
            score -= 0.15
            reasons.append("Imports preserved — suggests intentional refactoring")

    # Signal 2: Function count
    old_funcs = len(_FUNCTION_PATTERN.findall(existing_content))
    new_funcs = len(_FUNCTION_PATTERN.findall(new_content))
    if old_funcs > 3:
        func_ratio = new_funcs / old_funcs
        if func_ratio < 0.5:
            score += 0.2
            reasons.append(f"Function loss: {old_funcs} → {new_funcs}")
        elif func_ratio >= 0.8:
            score -= 0.1
            reasons.append("Function count preserved")

    # Signal 3: Export preservation
    old_exports = len(_EXPORT_PATTERN.findall(existing_content))
    new_exports = len(_EXPORT_PATTERN.findall(new_content))
    if old_exports > 0:
        export_ratio = new_exports / old_exports
        if export_ratio < 0.5:
            score += 0.15
            reasons.append(f"Export loss: {old_exports} → {new_exports} — API surface damaged")

    # Signal 4: Tail abruptness — truncated files often end mid-statement
    tail = new_content.rstrip()
    if tail and not tail.endswith(("}", ";", ")", "]", "\n", '"', "'", "`", "#")):
        score += 0.15
        reasons.append(f"File ends abruptly (last char: '{tail[-1]}') — likely truncation")

    score = max(0.0, min(1.0, score))

    return {
        "is_truncation": score > 0.5,
        "confidence": round(score, 2),
        "reasoning": "; ".join(reasons) if reasons else "Neutral assessment",
    }


def check_content_regression_with_evidence(
    abs_path: str,
    new_content: str,
    force: bool = False,
    project_dir: str = "",
) -> Optional[str]:
    """Full L1+L2 content regression check with evidence persistence."""
    l1_result = check_content_regression(abs_path, new_content, force)

    if l1_result is None:
        # L1 passed — no regression detected
        if project_dir:
            write_evidence(project_dir, "content_regression_evidence", {
                "l1_passed": True, "l2_invoked": False,
                "file": os.path.basename(abs_path),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return None

    # L1 blocked — run L2 to see if it's intentional refactoring
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            existing_content = f.read()
    except Exception:
        existing_content = ""

    l2 = score_regression_confidence(existing_content, new_content, abs_path)

    evidence = {
        "l1_passed": False,
        "l2_invoked": True,
        "l2_is_truncation": l2["is_truncation"],
        "l2_confidence": l2["confidence"],
        "l2_reasoning": l2["reasoning"],
        "file": os.path.basename(abs_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if project_dir:
        write_evidence(project_dir, "content_regression_evidence", evidence)

    # L2 can override L1 if confident it's a real refactoring
    if not l2["is_truncation"] and l2["confidence"] < 0.35:
        logger.info(
            f"[CONTENT REGRESSION GUARD] L2 override: regression on "
            f"{os.path.basename(abs_path)} appears to be intentional refactoring "
            f"(confidence={l2['confidence']:.2f})"
        )
        return None  # Allow the write

    return l1_result  # Maintain the block
