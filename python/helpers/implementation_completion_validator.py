"""Implementation Completion Validator — Evidence-only (Cat 4A simplified).

Collects evidence about what files were created/modified during a delegated
IMPLEMENTATION phase.  Does NOT block — `passed` is always True.
The delegation status is the real signal (ITR-55 P1).

History:
    Originally used a 4-signal weighted scoring system (ADR-82) to gate
    completion.  Scoring was stripped (Cat 4A) because:
    - phase_completion_guard.py already ignores the blocking verdict
    - The subordinate's code_self_check validates build + TDD green
    - The scoring was dead logic

Functions:
    take_file_snapshot(project_dir) → FileSnapshot
        Captures file paths AND line counts for delta comparison.

    validate_implementation_completion(project_dir, phase_seq, ...) → dict
        Evidence collection with structured output.  Always passed=True.

Consumer:
    - phase_completion_guard.py (orchestrator gate — reads evidence fields)
    - call_subordinate.py (pre-delegation snapshot)

Depends on:
    - python.helpers.source_scanner  (SOURCE_EXTENSIONS, EXCLUDE_DIRS)
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Union

from python.helpers.source_scanner import EXCLUDE_DIRS, SOURCE_EXTENSIONS, list_project_files

logger = logging.getLogger("agent.implementation_completion_validator")

# ──────────────────────────────────────────────────────────────────────
# File classification patterns (kept from original)
# ──────────────────────────────────────────────────────────────────────

_TEST_PATTERNS: List[re.Pattern] = [
    re.compile(r"\.test\.", re.IGNORECASE),
    re.compile(r"\.spec\.", re.IGNORECASE),
    re.compile(r"(^|/)__tests__/", re.IGNORECASE),
    re.compile(r"(^|/)tests?/", re.IGNORECASE),
]

_CONFIG_EXTENSIONS: Set[str] = {
    ".json", ".yaml", ".yml", ".toml",
    ".env", ".prisma", ".graphql",
    ".lock", ".cfg", ".ini",
}

_CONFIG_NAME_PATTERNS: List[re.Pattern] = [
    re.compile(r"^\.env", re.IGNORECASE),
    re.compile(r"\.config\.", re.IGNORECASE),
]

# ──────────────────────────────────────────────────────────────────────
# FileSnapshot dataclass
# ──────────────────────────────────────────────────────────────────────

@dataclass
class FileSnapshot:
    """Pre-delegation file state capture.

    Captures both file paths AND line counts so the validator can detect
    modifications (line growth), not just new files.

    Attributes:
        files:        Set of relative file paths.
        line_counts:  Map of relpath → line count.
        timestamp:    ISO timestamp of when snapshot was taken.
    """
    files: Set[str] = field(default_factory=set)
    line_counts: Dict[str, int] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────
# File snapshot
# ──────────────────────────────────────────────────────────────────────

def take_file_snapshot(project_dir: str) -> FileSnapshot:
    """Return a FileSnapshot with file paths and line counts.

    Uses SOURCE_EXTENSIONS and EXCLUDE_DIRS from source_scanner for
    consistency with the rest of the framework.

    Args:
        project_dir: Absolute path to the project root.

    Returns:
        FileSnapshot with files set and line_counts dict.
        Returns empty FileSnapshot if the directory does not exist.
    """
    if not os.path.isdir(project_dir):
        return FileSnapshot()

    # OVL-3: Use centralized scanner instead of inline os.walk
    abs_paths = list_project_files(
        project_dir,
        extensions=SOURCE_EXTENSIONS,
        skip_dirs=EXCLUDE_DIRS,
    )

    paths: Set[str] = set()
    line_counts: Dict[str, int] = {}
    for fpath in abs_paths:
        rel = os.path.relpath(fpath, project_dir)
        paths.add(rel)
        line_counts[rel] = _count_lines(project_dir, rel)

    return FileSnapshot(files=paths, line_counts=line_counts)


# ──────────────────────────────────────────────────────────────────────
# Classification helpers (kept from original)
# ──────────────────────────────────────────────────────────────────────

def _is_test_file(relpath: str) -> bool:
    """Return True if *relpath* looks like a test/spec file."""
    for pat in _TEST_PATTERNS:
        if pat.search(relpath):
            return True
    return False


def _is_config_file(relpath: str) -> bool:
    """Return True if *relpath* looks like a config / environment file."""
    ext = os.path.splitext(relpath)[1].lower()
    if ext in _CONFIG_EXTENSIONS:
        return True
    basename = os.path.basename(relpath)
    for pat in _CONFIG_NAME_PATTERNS:
        if pat.search(basename):
            return True
    return False


def _count_lines(project_dir: str, relpath: str) -> int:
    """Count lines in a single file, returning 0 on any read error."""
    try:
        full = os.path.join(project_dir, relpath)
        with open(full, "r", errors="replace") as fh:
            return sum(1 for _ in fh)
    except (OSError, IOError):
        return 0


# ──────────────────────────────────────────────────────────────────────
# Line growth detection (kept for evidence — no scoring)
# ──────────────────────────────────────────────────────────────────────

def _detect_line_growth(
    pre_line_counts: Dict[str, int],
    current_line_counts: Dict[str, int],
) -> tuple:
    """Detect net line growth in modified files.

    Args:
        pre_line_counts:     {relpath: line_count} from pre-delegation snapshot.
        current_line_counts: {relpath: line_count} from current state.

    Returns:
        (total_growth, modified_files) where total_growth is clamped to ≥0
        and modified_files is a list of files that grew.
    """
    total_growth = 0
    modified_files: List[str] = []
    for relpath in pre_line_counts:
        if relpath not in current_line_counts:
            continue
        growth = current_line_counts[relpath] - pre_line_counts[relpath]
        if growth > 0:
            total_growth += growth
            modified_files.append(relpath)
    return max(total_growth, 0), modified_files


# ──────────────────────────────────────────────────────────────────────
# Core validator — evidence-only (no scoring)
# ──────────────────────────────────────────────────────────────────────

def validate_implementation_completion(
    project_dir: str,
    phase_seq: str,
    pre_delegation_files: Optional[Set[str]] = None,
    pre_delegation_snapshot: Optional[FileSnapshot] = None,
) -> Dict:
    """Collect evidence about what an IMPLEMENTATION phase produced.

    Always returns passed=True — delegation status is the real signal.
    Evidence fields (new_files, total_lines_added, etc.) are preserved
    for audit trail and downstream consumers.

    Backward compatible: accepts either pre_delegation_files (Set[str])
    or pre_delegation_snapshot (FileSnapshot).

    Args:
        project_dir:  Absolute path to the project root.
        phase_seq:    Phase sequence identifier (e.g. "3.1").
        pre_delegation_files:    LEGACY: Set of relative paths (backward compat).
        pre_delegation_snapshot: NEW: FileSnapshot with files + line_counts.

    Returns:
        A dict with keys:
            passed (bool):              Always True.
            new_files (list[str]):       Relative paths of new source files.
            modified_files (list[str]):  Files with line growth.
            new_file_count (int):        Count of new source files.
            modified_file_count (int):   Count of modified source files.
            total_lines_added (int):     Net lines added (new + growth).
            reason (str):               Human-readable evidence summary.
    """
    # Graceful handling: non-existent project dir
    if not os.path.isdir(project_dir):
        logger.warning(
            "[IMPL VALIDATOR] project_dir does not exist: %s", project_dir,
        )
        return _empty_result(
            f"Phase {phase_seq}: project directory does not exist",
        )

    # ── Normalize input: support both old Set[str] and new FileSnapshot ──
    if pre_delegation_snapshot is not None:
        pre_files = pre_delegation_snapshot.files
        pre_line_counts = pre_delegation_snapshot.line_counts
    elif pre_delegation_files is not None:
        pre_files = pre_delegation_files
        pre_line_counts = {}  # no line counts available (degraded mode)
    else:
        pre_files = set()
        pre_line_counts = {}

    # ── Take current snapshot ──
    current_snapshot = take_file_snapshot(project_dir)
    current_files = current_snapshot.files
    current_line_counts = current_snapshot.line_counts

    # ── New source files ──
    delta = current_files - pre_files
    new_source: List[str] = []
    for relpath in sorted(delta):
        if not _is_test_file(relpath) and not _is_config_file(relpath):
            new_source.append(relpath)
    new_lines = sum(_count_lines(project_dir, f) for f in new_source)

    # ── Line growth in modified files ──
    total_growth, modified_files = _detect_line_growth(
        pre_line_counts, current_line_counts,
    )
    # Filter modified_files to exclude test/config
    modified_source = [f for f in modified_files
                       if not _is_test_file(f) and not _is_config_file(f)]

    # ── Total lines added (new + growth) ──
    total_lines_added = new_lines + max(total_growth, 0)

    # ── Build informational reason string (no PASS/FAIL) ──
    parts: List[str] = []
    if new_source:
        parts.append(f"{len(new_source)} new file(s)")
    if modified_source:
        parts.append(f"{len(modified_source)} modified file(s) (+{total_growth} lines)")
    if parts:
        reason = f"Phase {phase_seq}: {', '.join(parts)}, {total_lines_added} total lines added"
    else:
        reason = f"Phase {phase_seq}: no new source files detected"

    logger.info(
        "[IMPL VALIDATOR] phase=%s new_source=%d modified=%d lines_added=%d",
        phase_seq, len(new_source), len(modified_source), total_lines_added,
    )

    return {
        "passed": True,
        "new_files": new_source,
        "modified_files": modified_source,
        "new_file_count": len(new_source),
        "modified_file_count": len(modified_source),
        "total_lines_added": total_lines_added,
        "reason": reason,
    }


def _empty_result(reason: str) -> Dict:
    """Return an evidence-only result with zero evidence."""
    return {
        "passed": True,
        "new_files": [],
        "modified_files": [],
        "new_file_count": 0,
        "modified_file_count": 0,
        "total_lines_added": 0,
        "reason": reason,
    }
