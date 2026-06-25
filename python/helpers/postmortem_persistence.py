"""
Postmortem Persistence — Testable delegation failure persistence.

F-4B: When subordinates fail, their error context is saved to
memory-bank/delegation-error-log.md so re-delegations carry
forward learnings about what was tried and what failed.

Usage:
    from python.helpers.postmortem_persistence import (
        save_postmortem,
        load_postmortem,
        build_redelegation_context,
    )

    # After subordinate fails
    save_postmortem(project_dir, {
        "errors": [{"summary": "Build failed 36x", "category": "build"}],
        "attempted_fixes": [{"fix": "removed use client"}],
    })

    # Before re-delegating
    context = build_redelegation_context(project_dir, original_prompt)
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("agix.postmortem_persistence")


def save_postmortem(project_dir: str, failure_data: dict) -> str:
    """Save failure context to memory-bank/delegation-error-log.md.

    Appends to existing file — does NOT overwrite. Each postmortem
    entry is timestamped for chronological ordering.

    Args:
        project_dir: Root project directory
        failure_data: Dict with 'errors' and 'attempted_fixes' lists

    Returns:
        Path to the saved log file.
    """
    memory_bank_dir = os.path.join(project_dir, "memory-bank")
    os.makedirs(memory_bank_dir, exist_ok=True)

    log_path = os.path.join(memory_bank_dir, "delegation-error-log.md")

    errors = failure_data.get("errors", [])
    fixes = failure_data.get("attempted_fixes", [])
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        f"\n## Delegation Failure — {timestamp}\n",
    ]

    if errors:
        lines.append("### Errors")
        for err in errors:
            summary = err.get("summary", "unknown")
            category = err.get("category", "unknown")
            lines.append(f"- **[{category}]** {summary}")
        lines.append("")

    if fixes:
        lines.append("### What Was Tried")
        for fix in fixes:
            fix_desc = fix.get("fix", "") if isinstance(fix, dict) else str(fix)
            lines.append(f"- {fix_desc}")
        lines.append("")

    lines.append("---\n")

    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"[POSTMORTEM] Saved {len(errors)} errors + {len(fixes)} fixes to {log_path}")
    return log_path


def load_postmortem(project_dir: str) -> str:
    """Load the delegation error log content.

    Returns empty string if no log exists.
    """
    log_path = os.path.join(project_dir, "memory-bank", "delegation-error-log.md")
    if not os.path.exists(log_path):
        return ""

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"[POSTMORTEM] Failed to read {log_path}: {e}")
        return ""


def build_redelegation_context(
    project_dir: str,
    original_prompt: str,
) -> str:
    """Build a re-delegation prompt that includes previous failure context.

    If no postmortem exists, returns just the original prompt (no overhead).

    Args:
        project_dir: Root project directory
        original_prompt: The original delegation message

    Returns:
        New prompt string with failure context prepended (if any),
        or original prompt unchanged (if no failures).
    """
    postmortem = load_postmortem(project_dir)

    if not postmortem.strip():
        return original_prompt

    return (
        "## ⚠️ PREVIOUS FAILURES — READ BEFORE STARTING\n\n"
        "The previous subordinate failed. Here is what went wrong "
        "and what was already tried. You MUST use a DIFFERENT approach.\n\n"
        f"{postmortem}\n\n"
        "---\n\n"
        f"## Original Task\n\n{original_prompt}"
    )
