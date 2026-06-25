"""
Hardstop File Integrity Checker — Fix 3 (ISS-03)

After a HARD_STOP fires (same_message_bridge detects 3+ identical messages),
the agent may have applied partial edits via replace_in_file that left files
in a corrupted state (e.g., same code block duplicated 5x).

This module provides:
- detect_duplicated_blocks(content) → scans file content for duplicated blocks
- check_file_integrity_after_hardstop(project_dir, modified_files) → scans files

Design:
- A "duplicated block" is a contiguous sequence of 3+ non-blank lines that
  appears 2+ times in the file (identical content).
- Single-line duplicates (e.g., repeated import statements) are excluded.
- Uses a sliding window approach: hash each N-line window, detect collisions.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

logger = logging.getLogger("agix.hardstop_file_integrity")

# Minimum number of contiguous lines to consider a "block"
MIN_BLOCK_LINES = 3

# Maximum number of files to scan in a single check (safety cap)
MAX_FILES_TO_SCAN = 50


@dataclass
class DuplicatedBlock:
    """A single duplicated block found in a file."""
    block_text: str           # The text of the duplicated block
    occurrence_count: int     # How many times this block appears
    line_numbers: List[int]   # Starting line numbers of each occurrence (1-indexed)
    num_lines: int            # Number of lines in the block


@dataclass
class IntegrityResult:
    """Result of scanning a file for corruption."""
    is_corrupted: bool = False
    duplicated_blocks: List[DuplicatedBlock] = field(default_factory=list)


def _normalize_line(line: str) -> str:
    """Normalize a line for comparison: strip trailing whitespace."""
    return line.rstrip()


def detect_duplicated_blocks(
    content: str,
    min_block_lines: int = MIN_BLOCK_LINES,
) -> IntegrityResult:
    """Scan file content for duplicated multi-line blocks.

    A duplicated block is a contiguous sequence of `min_block_lines` or more
    non-blank lines that appears 2+ times in the file. This is the signature
    corruption pattern from replace_in_file partial-edit + re-application.

    Args:
        content: The file content to scan.
        min_block_lines: Minimum lines to consider a "block" (default: 3).

    Returns:
        IntegrityResult with is_corrupted=True if duplicated blocks found.
    """
    if not content or not content.strip():
        return IntegrityResult()

    lines = content.split("\n")
    # Filter out pure blank lines for block detection
    # but keep line numbers for reporting
    indexed_lines: List[Tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            indexed_lines.append((i + 1, _normalize_line(line)))  # 1-indexed

    if len(indexed_lines) < min_block_lines:
        return IntegrityResult()

    # Sliding window: for each window size from min_block_lines up to half
    # the file, hash the window content and find duplicates.
    # We iterate from largest to smallest to find the biggest duplicates first.
    max_window = min(len(indexed_lines) // 2, 50)  # Cap window size

    found_blocks: List[DuplicatedBlock] = []
    # Track which line ranges we've already flagged to avoid sub-block noise
    flagged_ranges: Set[Tuple[int, int]] = set()

    for window_size in range(max_window, min_block_lines - 1, -1):
        # Hash each window
        window_hashes: Dict[str, List[int]] = {}  # hash → list of start positions in indexed_lines

        for start in range(len(indexed_lines) - window_size + 1):
            window = indexed_lines[start:start + window_size]
            # Create hash from line content (ignoring line numbers)
            block_key = "\n".join(line for _, line in window)
            if block_key not in window_hashes:
                window_hashes[block_key] = []
            window_hashes[block_key].append(start)

        # Find blocks that appear 2+ times
        for block_key, positions in window_hashes.items():
            if len(positions) < 2:
                continue

            # Check if any of these positions overlap with already-flagged ranges
            # (skip sub-blocks of already-detected larger duplicates)
            all_flagged = True
            for pos in positions:
                start_line = indexed_lines[pos][0]
                end_line = indexed_lines[pos + window_size - 1][0]
                is_sub_range = False
                for flagged_start, flagged_end in flagged_ranges:
                    if start_line >= flagged_start and end_line <= flagged_end:
                        is_sub_range = True
                        break
                if not is_sub_range:
                    all_flagged = False
                    break

            if all_flagged:
                continue

            # Record this as a duplicated block
            line_numbers = [indexed_lines[pos][0] for pos in positions]
            found_blocks.append(DuplicatedBlock(
                block_text=block_key,
                occurrence_count=len(positions),
                line_numbers=line_numbers,
                num_lines=window_size,
            ))

            # Mark these ranges as flagged
            for pos in positions:
                start_line = indexed_lines[pos][0]
                end_line = indexed_lines[pos + window_size - 1][0]
                flagged_ranges.add((start_line, end_line))

    if found_blocks:
        return IntegrityResult(
            is_corrupted=True,
            duplicated_blocks=found_blocks,
        )

    return IntegrityResult()


def check_file_integrity_after_hardstop(
    project_dir: str,
    modified_files: List[str],
) -> List[dict]:
    """Scan recently-modified files for corruption after a HARD_STOP.

    This is the entry point called after the escape hatch fires. It reads
    each file and runs detect_duplicated_blocks() on its content.

    Args:
        project_dir: The project directory (for logging context).
        modified_files: List of absolute file paths to check.

    Returns:
        List of dicts with keys: file, is_corrupted, details.
        Only corrupted files are included in the result.
    """
    results: List[dict] = []

    for filepath in modified_files[:MAX_FILES_TO_SCAN]:
        if not os.path.isfile(filepath):
            logger.debug(f"[FILE_INTEGRITY] Skipping non-existent file: {filepath}")
            continue

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"[FILE_INTEGRITY] Failed to read {filepath}: {e}")
            continue

        result = detect_duplicated_blocks(content)
        if result.is_corrupted:
            details = []
            for block in result.duplicated_blocks:
                details.append({
                    "block_text_preview": block.block_text[:200],
                    "occurrence_count": block.occurrence_count,
                    "line_numbers": block.line_numbers,
                    "num_lines": block.num_lines,
                })
            results.append({
                "file": filepath,
                "is_corrupted": True,
                "details": details,
            })
            logger.warning(
                f"[FILE_INTEGRITY] CORRUPTION DETECTED in {filepath}: "
                f"{len(result.duplicated_blocks)} duplicated block(s)"
            )

    return results
